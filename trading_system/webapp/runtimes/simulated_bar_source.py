"""Simulated bar source for the paper-trading dashboard.

REQ refs:
- REQ_F_PAP_001 — paper-trading runtime composes a ``BarSource``.
  This is a v1 deterministic stub so operators can validate the
  end-to-end wizard → dashboard → SSE flow without yfinance
  wiring (REQ_F_PAP_002 yfinance adapter lands as a follow-up).
- REQ_NF_DET_001 — same seed + same call sequence ⇒ identical
  bar stream so the dashboard is replay-deterministic in tests
  and demos.

Each ``next_bar()`` call advances the internal clock by
``step_seconds`` (default 60s of "market time" per emitted bar)
and emits a Gaussian random-walk close. The internal RNG is
seeded from the caller-supplied ``seed`` so two operators with
distinct ``account_id`` derive distinct paths while each
account's path is reproducible.

Not a market-data adapter — does NOT satisfy the
``trading_system.data.provider.MarketDataProvider`` Protocol.
It only satisfies the runtime-local ``BarSource`` Protocol in
``trading_system.webapp.runtimes.paper_trading``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId
from trading_system.result import Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class SimulatedBarSource:
    """Deterministic Gaussian-random-walk bar generator.

    Each call to ``next_bar()`` returns ``Ok(Some(Bar))``. Empty /
    no-new-bar Ok shapes are unreachable — the simulator always
    has a next bar to emit. ``latest_cached()`` mirrors the most
    recently emitted bar (useful for the runtime's cache-fallback
    code path even though this source never errs).
    """

    instrument_id: InstrumentId
    seed: int = 0
    # Starting close price; the random walk multiplies through it.
    base_price: Decimal = Decimal("100.00")
    # Per-step Gaussian drift in basis points (positive ⇒ trending up).
    drift_bps: Decimal = Decimal("0")
    # Per-step Gaussian volatility in basis points (1 bp = 0.01%).
    vol_bps: Decimal = Decimal("50")
    # Wall-clock duration each emitted bar represents.
    step_seconds: int = 60
    # Wall-clock anchor for the FIRST emitted bar. ``None`` ⇒ use
    # ``datetime.now(UTC)`` at the first call.
    start_at: datetime | None = None

    _rng: random.Random = field(init=False, repr=False)
    _last_at: datetime | None = field(default=None, init=False)
    _last_bar: Bar | None = field(default=None, init=False)
    _last_close: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.step_seconds <= 0:
            raise ValueError(
                f"SimulatedBarSource.step_seconds must be > 0, "
                f"got {self.step_seconds}"
            )
        if self.base_price <= 0:
            raise ValueError(
                f"SimulatedBarSource.base_price must be > 0, "
                f"got {self.base_price}"
            )
        self._rng = random.Random(self.seed)
        self._last_close = self.base_price

    def next_bar(self) -> Result[Option[Bar], str]:
        """Advance the simulator one step + return the new bar."""
        if self._last_at is None:
            now = self.start_at or datetime.now(tz=UTC)
        else:
            now = self._last_at + timedelta(seconds=self.step_seconds)

        # Gaussian return on the last close.
        z = Decimal(repr(self._rng.gauss(0, 1)))
        return_decimal = (self.drift_bps + self.vol_bps * z) / Decimal("10000")
        raw_close = self._last_close * (Decimal("1") + return_decimal)
        new_close = raw_close.quantize(Decimal("0.01"))
        if new_close <= 0:
            new_close = Decimal("0.01")

        # OHLC envelope around the move — small bounds for a
        # synthetic feed; volume is constant.
        prev = self._last_close
        envelope_low = min(prev, new_close) * Decimal("0.999")
        envelope_high = max(prev, new_close) * Decimal("1.001")
        bar = Bar(
            at=now,
            open=prev,
            high=envelope_high.quantize(Decimal("0.01")),
            low=envelope_low.quantize(Decimal("0.01")),
            close=new_close,
            volume=Decimal("1000"),
        )
        self._last_at = now
        self._last_bar = bar
        self._last_close = new_close
        return Ok(Some(bar))

    def latest_cached(self) -> Result[Option[Bar], str]:
        """Return the most recently emitted bar, if any.

        Used by the runtime's graceful-degradation path
        (REQ_F_PAP_002) — this source never errs, so the path is
        only exercised by tests that swap a flaky source in.
        """
        if self._last_bar is None:
            return Ok(Nothing())
        return Ok(Some(self._last_bar))
