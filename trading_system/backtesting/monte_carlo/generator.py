"""``MCGenerator`` Protocol + closed v1 generator set.

REQ refs:
- REQ_F_MCS_002 — closed v1 set: BlockBootstrap / GBM / RegimeStitched.
- REQ_SDS_MCS_004 — single-method ``generate(historical_bars, *, seed,
  n_steps) -> Result[tuple[Bar, ...], MonteCarloError]`` Protocol surface.
- REQ_SDD_MCS_003 — per-path seed seeds a local ``random.Random``; no
  generator reads process-global RNG state.

Synthetic bars produced by every generator preserve the historical
opening bar at index 0 (so anyone slicing ``[0]`` against the historical
series sees the same close), then compounds returns from there. OHLC
fields beyond ``close`` are filled with a deterministic
``open == high == low == close``/``volume = 1`` shape — sufficient for
strategy code that consumes the close as the decision price and ignores
intraday detail.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trading_system.backtesting.monte_carlo.config import GBMParams
from trading_system.backtesting.monte_carlo.errors import MonteCarloError
from trading_system.data.types import Bar
from trading_system.result import Err, Ok, Result


@runtime_checkable
class MCGenerator(Protocol):
    """REQ_F_MCS_002 / REQ_SDS_MCS_004 — single-method generator
    surface. Implementations SHALL be pure functions of
    ``(historical_bars, seed, n_steps)`` and SHALL NOT read
    process-global RNG state."""

    def generate(
        self,
        historical_bars: tuple[Bar, ...],
        *,
        seed: int,
        n_steps: int,
    ) -> Result[tuple[Bar, ...], MonteCarloError]: ...


def _historical_returns(bars: tuple[Bar, ...]) -> list[Decimal]:
    """Simple per-bar returns: ``close[t] / close[t-1] - 1``."""
    out: list[Decimal] = []
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        out.append(cur.close / prev.close - Decimal("1"))
    return out


def _step_delta(bars: tuple[Bar, ...]) -> timedelta:
    """Inter-bar spacing inferred from the historical series.

    Falls back to 1 day when the series is too short to compute.
    """
    if len(bars) < 2:
        return timedelta(days=1)
    return bars[1].at - bars[0].at


def _materialise_path(
    *,
    start_at: datetime,
    delta: timedelta,
    start_close: Decimal,
    returns: list[Decimal],
) -> tuple[Bar, ...]:
    """Build synthetic bars from a returns series.

    Every bar gets ``open == high == low == close`` so the OHLCV
    invariants in ``Bar.__post_init__`` hold; ``volume = 1`` is the
    deterministic placeholder. Strategy code that reads
    ``close`` (the decision price) gets the realistic random walk;
    intraday-detail consumers (which the deterministic engine doesn't
    have) would need a richer generator (out of scope for v1).
    """
    bars: list[Bar] = []
    price = start_close
    at = start_at
    # Index 0 is the anchor — emit the historical opening close so
    # callers slicing [0] see the same starting point.
    bars.append(
        Bar(
            at=at,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("1"),
        )
    )
    for r in returns:
        at = at + delta
        price = price * (Decimal("1") + r)
        if price <= 0:
            # GBM with extreme negative draws can push the price below
            # zero; clamp to a tiny positive so the Bar invariant holds.
            price = Decimal("0.0001")
        bars.append(
            Bar(
                at=at,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
            )
        )
    return tuple(bars)


@dataclass(frozen=True, slots=True)
class BlockBootstrapGenerator:
    """Draws blocks of ``block_length`` returns from the historical
    series with i.i.d. replacement and concatenates them until
    ``n_steps`` returns are produced. The trailing block is truncated.

    REQ_F_MCS_002 / REQ_SDD_MCS_003.
    """

    block_length: int

    def generate(
        self,
        historical_bars: tuple[Bar, ...],
        *,
        seed: int,
        n_steps: int,
    ) -> Result[tuple[Bar, ...], MonteCarloError]:
        if not historical_bars:
            return Err(MonteCarloError("mc:empty_history"))
        if self.block_length <= 0:
            return Err(MonteCarloError("mc:bad_block_length", str(self.block_length)))
        rng = random.Random(seed)
        returns = _historical_returns(historical_bars)
        if not returns:
            return Err(MonteCarloError("mc:empty_history", "len(bars) < 2"))
        block_starts = list(range(0, max(1, len(returns) - self.block_length + 1)))
        synthetic: list[Decimal] = []
        # Target n_steps returns ⇒ n_steps + 1 bars total (anchor + path).
        target_returns = max(0, n_steps - 1)
        while len(synthetic) < target_returns:
            start = rng.choice(block_starts)
            block = returns[start : start + self.block_length]
            synthetic.extend(block)
        synthetic = synthetic[:target_returns]
        return Ok(
            _materialise_path(
                start_at=historical_bars[0].at,
                delta=_step_delta(historical_bars),
                start_close=historical_bars[0].close,
                returns=synthetic,
            )
        )


@dataclass(frozen=True, slots=True)
class GBMGenerator:
    """Geometric Brownian motion: ``r_t = mu + sigma * z_t``,
    ``z_t ~ N(0, 1)`` via ``random.Random.gauss``.

    REQ_F_MCS_002 / REQ_SDD_MCS_003.
    """

    gbm_params: GBMParams

    def generate(
        self,
        historical_bars: tuple[Bar, ...],
        *,
        seed: int,
        n_steps: int,
    ) -> Result[tuple[Bar, ...], MonteCarloError]:
        if not historical_bars:
            return Err(MonteCarloError("mc:empty_history"))
        rng = random.Random(seed)
        mu = self.gbm_params.mu
        sigma = self.gbm_params.sigma
        target_returns = max(0, n_steps - 1)
        # Decimal-aware Normal: draw float ⇒ quantize. Sigma=0 ⇒ deterministic mu path.
        synthetic: list[Decimal] = []
        for _ in range(target_returns):
            z = Decimal(str(rng.gauss(0.0, 1.0)))
            synthetic.append(mu + sigma * z)
        return Ok(
            _materialise_path(
                start_at=historical_bars[0].at,
                delta=_step_delta(historical_bars),
                start_close=historical_bars[0].close,
                returns=synthetic,
            )
        )


@dataclass(frozen=True, slots=True)
class RegimeStitchedGenerator:
    """Labels each historical bar with a ``MarketRegime`` (via a caller-
    injected detector) and bootstraps per-regime sub-blocks of length
    ``regime_window``. Composes with CR-013's deterministic detector so
    REQ_NF_RGM_001 transitively guarantees REQ_NF_MCS_001 here.

    The detector is injected so the generator stays free of an import
    on ``regime/`` — keeps the import-graph audit (REQ_SDD_MCS_001) clean
    when callers don't need this generator. When ``detector is None``
    the generator falls back to plain block bootstrap with a single
    "all-bars" regime so the runner doesn't need to special-case it.
    """

    regime_window: int
    detector: object | None = None  # RegimeDetector Protocol; injected by runner

    def generate(
        self,
        historical_bars: tuple[Bar, ...],
        *,
        seed: int,
        n_steps: int,
    ) -> Result[tuple[Bar, ...], MonteCarloError]:
        if not historical_bars:
            return Err(MonteCarloError("mc:empty_history"))
        if self.regime_window <= 0:
            return Err(
                MonteCarloError("mc:config_mismatch:regime_window", str(self.regime_window))
            )
        rng = random.Random(seed)
        returns = _historical_returns(historical_bars)
        if not returns:
            return Err(MonteCarloError("mc:empty_history", "len(bars) < 2"))

        # Regime labelling — if no detector, use a single "default" label.
        labels = self._label_bars(historical_bars)

        # Partition returns by label.
        per_regime: dict[str, list[int]] = {}
        for idx, lab in enumerate(labels[1:], start=0):  # returns[i] = close[i+1]/close[i]-1
            per_regime.setdefault(lab, []).append(idx)
        if not per_regime:
            per_regime["default"] = list(range(len(returns)))

        # Bootstrap per-regime sub-blocks until target_returns is reached.
        target_returns = max(0, n_steps - 1)
        synthetic: list[Decimal] = []
        regime_names = sorted(per_regime.keys())
        while len(synthetic) < target_returns:
            lab = rng.choice(regime_names)
            indices = per_regime[lab]
            if not indices:
                continue
            start_idx = rng.choice(indices)
            block_end = min(start_idx + self.regime_window, len(returns))
            synthetic.extend(returns[start_idx:block_end])
        synthetic = synthetic[:target_returns]
        return Ok(
            _materialise_path(
                start_at=historical_bars[0].at,
                delta=_step_delta(historical_bars),
                start_close=historical_bars[0].close,
                returns=synthetic,
            )
        )

    def _label_bars(self, bars: tuple[Bar, ...]) -> list[str]:
        """Run a rolling-window labelling. ``self.detector`` SHOULD
        expose ``evaluate(bars: tuple[Bar, ...]) -> MarketRegime``; if
        absent every bar gets the ``"default"`` label."""
        detector = self.detector
        if detector is None:
            return ["default"] * len(bars)
        out: list[str] = []
        for i in range(len(bars)):
            lo = max(0, i - self.regime_window + 1)
            window = bars[lo : i + 1]
            try:
                regime = detector.evaluate(window)  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                out.append("default")
                continue
            out.append(str(getattr(regime, "value", regime)))
        return out


# ---------------------------------------------------------------------------
# Statistical helpers (used by tests / aggregators)
# ---------------------------------------------------------------------------


def percentile(values: list[Decimal], q: Decimal) -> Decimal:
    """Linear-interpolation percentile over a Decimal list.

    ``q`` is in ``[0, 1]``. Sorted ascending; the ``q``th percentile is
    the value at position ``q * (n - 1)`` with linear interpolation
    between neighbours.
    """
    if not values:
        return Decimal("0")
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = q * Decimal(n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    frac = rank - Decimal(lower)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * frac


def stddev_decimal(values: list[Decimal]) -> Decimal:
    """Sample standard deviation. Returns 0 for series of length <= 1."""
    n = len(values)
    if n <= 1:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(n)
    variance = sum(((v - mean) ** 2 for v in values), Decimal("0")) / Decimal(n - 1)
    return Decimal(str(math.sqrt(float(variance))))
