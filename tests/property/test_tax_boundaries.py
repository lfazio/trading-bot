"""Property-based tests for the tax engine — REQ_TP_STR_002.

The tax engine is pure math; properties below pin the invariants
across every input the law of math allows:

- ``net_gain(gross > 0)`` = ``gross × (1 - rate)`` (REQ_F_TAX_001)
- ``net_gain(gross < 0)`` returns the loss unchanged (REQ_F_TAX_006)
- ``net_dividend(gross >= 0)`` = ``gross × (1 - rate)`` (REQ_F_TAX_002)
- ``trade_passes_gate`` is strictly greater-than at the boundary
  (REQ_F_TAX_003 / REQ_C_BHV_003 — marginal trades auto-rejected)
- Rounding is HALF_UP to 2 decimals (REQ_SDD_ALG_001)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trading_system.models.money import Currency, Money
from trading_system.tax.config import TaxConfig
from trading_system.tax.engine import (
    net_dividend,
    net_gain,
    trade_passes_gate,
)


# Reasonable decimal strategy — positive amounts up to 10 million.
# Hypothesis's ``decimals`` honours places=2 so the assertions
# stay aligned with the engine's rounding.
_POSITIVE_AMOUNTS = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("10_000_000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_NEGATIVE_AMOUNTS = st.decimals(
    min_value=Decimal("-10_000_000"),
    max_value=Decimal("-0.01"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_RATES = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("0.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
# TaxConfig.gate_multiplier is typed `int`; use integers between 1 and 10.
_GATE_MULTIPLIERS = st.integers(min_value=1, max_value=10)


def _money(amount: Decimal) -> Money:
    return Money(amount=amount, currency=Currency.EUR)


def _cfg(rate: Decimal, gate_multiplier: int = 5) -> TaxConfig:
    return TaxConfig(rate=rate, gate_multiplier=gate_multiplier)


# ---------------------------------------------------------------------------
# net_gain
# ---------------------------------------------------------------------------


@given(gross=_POSITIVE_AMOUNTS, rate=_RATES)
@settings(max_examples=200)
def test_net_gain_applies_rate_for_positive_input(gross: Decimal, rate: Decimal) -> None:
    """REQ_F_TAX_001 — positive gain shrinks by ``rate``."""
    result = net_gain(_cfg(rate), _money(gross))
    expected_raw = gross * (Decimal(1) - rate)
    # Rounding: HALF_UP to 2 decimals. The engine rounds, so result
    # must equal the rounded expected.
    expected_rounded = expected_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert result.amount == expected_rounded


@given(loss=_NEGATIVE_AMOUNTS, rate=_RATES)
@settings(max_examples=100)
def test_net_gain_passes_losses_through(loss: Decimal, rate: Decimal) -> None:
    """REQ_F_TAX_006 — losses are not "taxed positively"; net_gain
    returns the loss unchanged (modulo rounding to cents)."""
    result = net_gain(_cfg(rate), _money(loss))
    # Loss should not be reduced by tax — should equal input
    # (within the 2-decimal rounding the engine applies).
    assert result.amount == loss.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert result.amount < 0


@given(gross=_POSITIVE_AMOUNTS, rate=_RATES)
@settings(max_examples=100)
def test_net_gain_never_exceeds_gross(gross: Decimal, rate: Decimal) -> None:
    """Sanity: post-tax never exceeds pre-tax for non-negative rates."""
    result = net_gain(_cfg(rate), _money(gross))
    # Allow ≤ rather than < because rate=0 is a valid edge.
    assert result.amount <= gross


@given(gross=_POSITIVE_AMOUNTS)
@settings(max_examples=50)
def test_net_gain_at_zero_rate_is_identity(gross: Decimal) -> None:
    """rate=0 ⇒ no tax taken (within rounding)."""
    result = net_gain(_cfg(Decimal("0")), _money(gross))
    assert result.amount == gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# net_dividend
# ---------------------------------------------------------------------------


@given(gross=_POSITIVE_AMOUNTS, rate=_RATES)
@settings(max_examples=200)
def test_net_dividend_applies_rate(gross: Decimal, rate: Decimal) -> None:
    """REQ_F_TAX_002 — dividends taxed at the same flat rate."""
    result = net_dividend(_cfg(rate), _money(gross))
    expected = (gross * (Decimal(1) - rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert result.amount == expected


@given(gross=_POSITIVE_AMOUNTS, rate=_RATES)
@settings(max_examples=50)
def test_net_dividend_equals_net_gain_on_positive_amounts(
    gross: Decimal, rate: Decimal
) -> None:
    """REQ_F_TAX_001 + REQ_F_TAX_002 — identical formula for the
    happy-path positive input."""
    cfg = _cfg(rate)
    assert net_dividend(cfg, _money(gross)) == net_gain(cfg, _money(gross))


# ---------------------------------------------------------------------------
# trade_passes_gate — strict greater-than at the boundary
# ---------------------------------------------------------------------------


@given(fees=_POSITIVE_AMOUNTS, multiplier=_GATE_MULTIPLIERS)
@settings(max_examples=100)
def test_gate_rejects_exact_boundary(
    fees: Decimal, multiplier: int
) -> None:
    """REQ_F_TAX_003 + REQ_C_BHV_003 — net == k × fees fails
    (strict greater-than). Marginal trades SHALL auto-reject."""
    cfg = _cfg(rate=Decimal("0.30"), gate_multiplier=multiplier)
    threshold = fees * multiplier
    # net == threshold exactly → must fail.
    assert not trade_passes_gate(
        cfg, _money(threshold), _money(fees)
    )


@given(
    fees=_POSITIVE_AMOUNTS,
    multiplier=_GATE_MULTIPLIERS,
    delta=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1"),
        places=2,
    ),
)
@settings(max_examples=100)
def test_gate_accepts_above_boundary(
    fees: Decimal, multiplier: int, delta: Decimal
) -> None:
    """REQ_F_TAX_003 — net > k × fees passes."""
    cfg = _cfg(rate=Decimal("0.30"), gate_multiplier=multiplier)
    threshold = fees * multiplier
    net = threshold + delta
    assert trade_passes_gate(cfg, _money(net), _money(fees))


@given(
    fees=_POSITIVE_AMOUNTS,
    multiplier=_GATE_MULTIPLIERS,
    delta=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1"),
        places=2,
    ),
)
@settings(max_examples=100)
def test_gate_rejects_below_boundary(
    fees: Decimal, multiplier: int, delta: Decimal
) -> None:
    """REQ_F_TAX_003 — net < k × fees fails."""
    cfg = _cfg(rate=Decimal("0.30"), gate_multiplier=multiplier)
    threshold = fees * multiplier
    net = threshold - delta
    assert not trade_passes_gate(cfg, _money(net), _money(fees))
