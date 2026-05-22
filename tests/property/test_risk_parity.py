"""Property-based tests for risk-parity weights — REQ_TP_STR_002.

REQ_F_STR_004 / REQ_SDD_ALG_010 — risk-parity weights are
inverse-vol-normalized. Properties verified:

- Weights are all > 0 (since realized_vol > 0 by construction).
- Weights sum to 1.0 ± 1 ulp (well within Decimal precision).
- Higher vol → lower weight (inverse-vol ordering).
- Identical-vol members get identical weights.

The properties exercise ``EnsembleStrategy.risk_parity_weights``
directly — the upstream strategy machinery is irrelevant for
testing the weighting math.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from trading_system.models.identifiers import StrategyId
from trading_system.models.phase import MarketRegime
from trading_system.models.trading import OrderType, Side
from trading_system.strategies.ensemble import EnsembleMember, EnsembleStrategy


# Minimal strategy stub satisfying the Strategy duck-type interface
# the ensemble's evaluate() expects. We never call evaluate in
# these property tests — risk_parity_weights only reads
# member.realized_vol.
class _StubStrategy:
    """Minimal Strategy duck — never invoked in these tests."""

    id: StrategyId

    def __init__(self, sid: str = "stub") -> None:
        self.id = StrategyId(sid)

    def evaluate(self, state: Any) -> list[Any]:  # pragma: no cover
        return []


_VOLS = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("2.0"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


def _ensemble(vols: list[Decimal]) -> EnsembleStrategy:
    members = [
        EnsembleMember(
            strategy=_StubStrategy(f"s{i}"),
            realized_vol=v,
        )
        for i, v in enumerate(vols)
    ]
    return EnsembleStrategy(
        members=members,
        target_vol=Decimal("0.10"),
        portfolio_vol_provider=lambda _state: Decimal("0.10"),
    )


# ---------------------------------------------------------------------------
# Weights sum to 1
# ---------------------------------------------------------------------------


@given(
    vols=st.lists(_VOLS, min_size=1, max_size=10).map(list),
)
@settings(max_examples=200)
def test_weights_sum_to_one(vols: list[Decimal]) -> None:
    """REQ_SDD_ALG_010 — risk-parity weights SHALL sum to 1.0
    (within Decimal arithmetic precision)."""
    ensemble = _ensemble(vols)
    weights = ensemble.risk_parity_weights()
    total = sum(weights, start=Decimal(0))
    # Sum can have small Decimal rounding from the division-then-
    # sum chain; assert within 1e-20 (way tighter than the
    # 1e-9 SDD invariant).
    assert abs(total - Decimal(1)) < Decimal("1e-20"), (
        f"weights sum to {total}, not 1"
    )


# ---------------------------------------------------------------------------
# All weights positive
# ---------------------------------------------------------------------------


@given(vols=st.lists(_VOLS, min_size=1, max_size=10))
@settings(max_examples=100)
def test_all_weights_positive(vols: list[Decimal]) -> None:
    """Inverse of a positive number is positive. The ensemble's
    EnsembleMember invariant guarantees ``realized_vol > 0``, so
    no weight can be zero or negative."""
    ensemble = _ensemble(vols)
    weights = ensemble.risk_parity_weights()
    assert all(w > 0 for w in weights), (
        f"non-positive weight in {weights}"
    )


# ---------------------------------------------------------------------------
# Higher vol → lower weight (inverse-vol ordering)
# ---------------------------------------------------------------------------


@given(
    vol_a=_VOLS,
    vol_b=_VOLS,
)
@settings(max_examples=100)
def test_higher_vol_yields_lower_weight(
    vol_a: Decimal, vol_b: Decimal
) -> None:
    """REQ_SDD_ALG_010 — inverse-vol weighting. Member with
    higher realized_vol SHALL receive lower weight."""
    assume(vol_a != vol_b)
    ensemble = _ensemble([vol_a, vol_b])
    weights = ensemble.risk_parity_weights()
    w_a, w_b = weights
    if vol_a > vol_b:
        assert w_a < w_b, (
            f"vol_a={vol_a} > vol_b={vol_b} but w_a={w_a} >= w_b={w_b}"
        )
    else:
        assert w_a > w_b


# ---------------------------------------------------------------------------
# Identical vols → identical weights
# ---------------------------------------------------------------------------


@given(
    vol=_VOLS,
    n=st.integers(min_value=2, max_value=8),
)
@settings(max_examples=50)
def test_identical_vols_yield_uniform_weights(
    vol: Decimal, n: int
) -> None:
    """n members with the same vol SHALL get weight 1/n each."""
    ensemble = _ensemble([vol] * n)
    weights = ensemble.risk_parity_weights()
    expected = Decimal(1) / Decimal(n)
    for w in weights:
        assert abs(w - expected) < Decimal("1e-20"), (
            f"expected {expected}, got {w}"
        )


# Avoid an unused-import warning for OrderType / Side / MarketRegime
# — they're handy reach-throughs for future property tests on
# evaluate() but not used in this slice.
_ = (OrderType, Side, MarketRegime)
