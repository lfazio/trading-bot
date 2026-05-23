"""``YFinanceBarSource`` — REQ_F_PAP_002 BarSource over the CR-009
yfinance adapter.

Wraps ``YFinanceMarketDataProvider.bars()`` with a rolling window
so the paper-trading runtime can poll for "current" bars. The
provider's ``run_mode="backtest"`` constraint stands — paper
trading is NOT live trading and SHALL NOT drive real-money
decisions (the operator's broker is still the LocalBrokerAdapter
simulation).

REQ refs:
- REQ_F_PAP_001 — BarSource Protocol satisfied.
- REQ_F_PAP_002 — graceful degradation: upstream block ->
  cached-only mode. ``next_bar`` returns categorised
  ``Err("data:upstream_blocked")`` on network failure so the
  paper runtime's ``_resolve_bar`` falls back to the cache.
- REQ_NF_DAT_001 — the cache is the system of record; replay
  determinism holds as long as the cache content holds.

Cadence: each ``next_bar`` call asks the provider for the last
``bar_window_days`` of bars + returns the freshest bar that is
strictly newer than the last one we surfaced. When yfinance
returns no new data (markets closed, no new bar since last
call), returns ``Ok(Nothing())`` so the runtime ticks without
re-firing the strategy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Bar, Timeframe
from trading_system.models.instrument import Instrument
from trading_system.models.money import Currency
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class YFinanceBarSource:
    """``BarSource`` Protocol adapter over a
    ``MarketDataProvider`` (the CR-009 yfinance provider).

    Construction parameters:
    - ``provider`` — wrapped market data provider. Type-hinted as
      the Protocol so tests inject mock providers without dragging
      in yfinance.
    - ``instrument`` — instrument the runtime is trading.
    - ``timeframe`` — bar granularity. Default ``DAILY``.
    - ``bar_window_days`` — how many days back ``next_bar`` asks
      the provider for on each poll. Default 7 (covers the
      previous trading week so weekends + holidays don't surface
      an Err).
    """

    provider: MarketDataProvider
    instrument: Instrument
    timeframe: Timeframe = Timeframe.D1
    bar_window_days: int = 7

    _last_emitted_at: datetime | None = field(default=None, init=False, repr=False)
    _last_bar: Bar | None = field(default=None, init=False, repr=False)
    # Kept here so the helper at module bottom can stash the
    # MarketDataProvider it built — the runtime reuses it as
    # ``market_data_provider`` for the strategy step too. Declared
    # as a slot-friendly field so the dataclass's ``slots=True``
    # doesn't reject the assignment.
    _provider: MarketDataProvider | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.bar_window_days <= 0:
            raise ValueError(
                f"YFinanceBarSource.bar_window_days must be > 0, "
                f"got {self.bar_window_days}"
            )

    def next_bar(self) -> Result[Option[Bar], str]:
        """Poll the provider for the freshest bar.

        Returns ``Ok(Some(bar))`` when a bar strictly newer than
        the last surfaced bar is available; ``Ok(Nothing())``
        when no new bar has arrived (markets closed / no data
        since the last poll); ``Err("data:upstream_blocked")``
        on a network failure so the paper-trading runtime's
        degradation path kicks in (REQ_F_PAP_002).
        """
        now = datetime.now(tz=UTC)
        start = now - timedelta(days=self.bar_window_days)
        result = self.provider.bars(
            self.instrument, self.timeframe, start, now
        )
        if isinstance(result, Err):
            # Categorise upstream-blocked into the documented
            # paper-runtime fallback code. Includes:
            # - network:* (curl errors, DNS, timeout)
            # - data:cache_miss_offline (no cached bar + offline)
            # - upstream:* (proxied through provider)
            # - data:not_found:<symbol> (yfinance returned an
            #   empty DataFrame because curl couldn't reach the
            #   upstream — symbol resolution is offline; if the
            #   ticker was genuinely delisted the cache would
            #   already hold older bars + the operator would see
            #   a stable last_close in the panel).
            # - data:rate_limited (yfinance throttled the call)
            reason = result.error
            if (
                "network" in reason
                or "cache_miss_offline" in reason
                or "upstream" in reason
                or "not_found" in reason
                or "rate_limited" in reason
            ):
                return Err("data:upstream_blocked")
            return Err(reason)
        bars = result.value
        if not bars:
            return Ok(Nothing())
        # The provider's contract says bars are sorted ascending
        # by ``Bar.at`` (REQ_SDD_API_007).
        latest = bars[-1]
        if (
            self._last_emitted_at is not None
            and latest.at <= self._last_emitted_at
        ):
            return Ok(Nothing())
        # Defensive price sanity — yfinance can return zero bars
        # on data-feed glitches.
        if latest.close <= Decimal("0") or latest.open <= Decimal("0"):
            return Err("data:upstream_blocked")
        self._last_emitted_at = latest.at
        self._last_bar = latest
        return Ok(Some(latest))

    def latest_cached(self) -> Result[Option[Bar], str]:
        """REQ_F_PAP_002 cached-fallback path.

        Returns the most recent bar we ever surfaced. The provider
        itself also has a ``latest()`` method that scans the disk
        cache; we prefer the in-memory snapshot so two-instance
        deployments (provider + bar source) stay independent.
        """
        if self._last_bar is None:
            # Try the provider's own latest() — it walks the
            # disk cache + returns the freshest persisted bar
            # for the instrument.
            try:
                latest = self.provider.latest(self.instrument)
            except Exception:  # noqa: BLE001 — defensive
                return Ok(Nothing())
            if isinstance(latest, Ok):
                return Ok(Some(latest.value))
            return Ok(Nothing())
        return Ok(Some(self._last_bar))


def build_yfinance_bar_source(
    *,
    instrument: Instrument,
    currency: Currency = Currency.EUR,
    cache_root_env_var: str = "TRADING_BOT_YFINANCE_CACHE",
    cache_root_default: str = "var/yfinance-cache",
) -> "YFinanceBarSource":
    """Construct a ``YFinanceBarSource`` over a disk-cached
    yfinance provider.

    Imports the CR-009 cache + provider lazily so the views
    layer doesn't drag them into its eager import set (the
    structural audit forbids ``trading_system.data.*`` reach
    from ``webapp/routers/views/``; this helper sits under
    ``webapp/runtimes/`` where the carve-out is documented).
    """
    from trading_system.data.yfinance.cache import YFinanceCache
    from trading_system.data.yfinance.provider import (
        YFinanceMarketDataProvider,
    )

    cache_root = Path(os.environ.get(cache_root_env_var, cache_root_default))
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = YFinanceCache(root=cache_root)
    provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=currency,
        allow_network=True,
        run_mode="backtest",
    )
    source = YFinanceBarSource(provider=provider, instrument=instrument)
    # Stash the provider so callers can use it as
    # market_data_provider for the strategy step too.
    source._provider = provider  # type: ignore[attr-defined]
    return source
