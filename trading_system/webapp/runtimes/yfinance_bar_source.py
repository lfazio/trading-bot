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
    # On the first ``next_bar`` call, fetch this many days of
    # historical bars and queue them up so the runtime ticks
    # through them in order. After the queue drains, subsequent
    # polls fetch the next freshest live bar. Without this the
    # paper-trading panel would sit at "0 ticks" until tomorrow
    # because daily bars only update once per day after market
    # close.
    backfill_days: int = 90
    # Cached bar history accumulated across polls. Surfaced to the
    # dashboard reader via ``history()`` so the sparkline + main
    # price chart see the full series without re-polling.
    _bar_history: list[Bar] = field(
        default_factory=list, init=False, repr=False
    )

    _last_emitted_at: datetime | None = field(default=None, init=False, repr=False)
    _last_bar: Bar | None = field(default=None, init=False, repr=False)
    _backfill_queue: list[Bar] | None = field(
        default=None, init=False, repr=False
    )
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

    def _classify_provider_err(self, reason: str) -> str:
        """Map the provider's Err categories into the documented
        ``data:upstream_blocked`` code so the paper-runtime's
        graceful-degradation path kicks in for transient outages.

        Maps: network:* / cache_miss_offline / upstream:* /
        not_found:* / rate_limited:* → upstream_blocked. Other
        codes surface unchanged.
        """
        if (
            "network" in reason
            or "cache_miss_offline" in reason
            or "upstream" in reason
            or "not_found" in reason
            or "rate_limited" in reason
        ):
            return "data:upstream_blocked"
        return reason

    def _prime_backfill(self) -> Result[Option[Bar], str]:
        """First-call hook: fetch ``backfill_days`` of bars + queue
        them up so subsequent ``next_bar`` calls stream the
        historical series instead of returning Nothing while
        waiting for tomorrow's bar."""
        now = datetime.now(tz=UTC)
        start = now - timedelta(days=self.backfill_days)
        result = self.provider.bars(
            self.instrument, self.timeframe, start, now
        )
        if isinstance(result, Err):
            self._backfill_queue = []  # mark primed even on failure
            return Err(self._classify_provider_err(result.error))
        bars = result.value
        # Sort defensively — the provider's contract says ascending
        # but mis-categorised data still surfaces in the dashboard.
        self._backfill_queue = sorted(bars, key=lambda b: b.at)
        return Ok(Nothing())  # caller advances to drain

    def next_bar(self) -> Result[Option[Bar], str]:
        """Stream the next bar.

        First call primes the backfill queue (last ``backfill_days``
        bars). Subsequent calls pop one queued bar per call until
        the queue empties; after that, polls live for fresh bars.

        Returns ``Ok(Some(bar))`` when a bar strictly newer than
        the last surfaced bar is available; ``Ok(Nothing())``
        when no new bar has arrived; ``Err("data:upstream_blocked")``
        on a network failure so the paper-runtime's degradation
        path kicks in (REQ_F_PAP_002).
        """
        # Prime the backfill queue on first call.
        if self._backfill_queue is None:
            prime_result = self._prime_backfill()
            if isinstance(prime_result, Err):
                return prime_result

        # Drain the backfill queue first.
        while self._backfill_queue:
            bar = self._backfill_queue.pop(0)
            if (
                self._last_emitted_at is not None
                and bar.at <= self._last_emitted_at
            ):
                continue
            if bar.close <= Decimal("0") or bar.open <= Decimal("0"):
                continue  # skip poisoned bars rather than crashing
            self._last_emitted_at = bar.at
            self._last_bar = bar
            self._bar_history.append(bar)
            return Ok(Some(bar))

        # Backfill empty — poll the upstream for any newer bars.
        now = datetime.now(tz=UTC)
        start = now - timedelta(days=self.bar_window_days)
        result = self.provider.bars(
            self.instrument, self.timeframe, start, now
        )
        if isinstance(result, Err):
            return Err(self._classify_provider_err(result.error))
        bars = result.value
        if not bars:
            return Ok(Nothing())
        latest = bars[-1]
        if (
            self._last_emitted_at is not None
            and latest.at <= self._last_emitted_at
        ):
            return Ok(Nothing())
        if latest.close <= Decimal("0") or latest.open <= Decimal("0"):
            return Err("data:upstream_blocked")
        self._last_emitted_at = latest.at
        self._last_bar = latest
        self._bar_history.append(latest)
        return Ok(Some(latest))

    def history(self) -> tuple[Bar, ...]:
        """Bars surfaced so far (backfill + live polls combined).

        The reader prefers this over the simulated source's
        history() when an instance is wired in, so the dashboard
        sparkline + price chart see the full accumulated series."""
        return tuple(self._bar_history)

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
