"""Tests for ``trading_system.wealth_ops.fx_hedger.hedger``.

Covers TC_FXH_004 (threshold gating) + TC_FXH_005 (proposal notional)
+ TC_FXH_006 (deterministic Currency.value sort order).

REQ refs: REQ_F_FXH_003, REQ_NF_FXH_001, REQ_SDD_FXH_002.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.money import Currency, Money
from trading_system.wealth_ops.fx_hedger.hedger import FXHedger
from trading_system.wealth_ops.fx_hedger.policy import HedgePolicy


_AT = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), Currency.EUR)


# ---------------------------------------------------------------------------
# TC_FXH_004 — threshold gating
# ---------------------------------------------------------------------------


def test_threshold_filters_below_or_equal() -> None:
    hedger = FXHedger(policy=HedgePolicy(threshold_pct=Decimal("0.05")))
    proposals = hedger.propose_hedges(
        exposures={Currency.USD: Decimal("0.20"), Currency.GBP: Decimal("0.04")},
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    # GBP below threshold — filtered.
    assert len(proposals) == 1
    assert proposals[0].currency is Currency.USD


def test_threshold_is_strict_greater_than() -> None:
    """REQ_SDD_FXH_002 — strict ``>``. A currency at exactly the
    threshold SHALL NOT produce a proposal."""
    hedger = FXHedger(policy=HedgePolicy(threshold_pct=Decimal("0.05")))
    proposals = hedger.propose_hedges(
        exposures={Currency.USD: Decimal("0.05")},  # equal — filtered
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    assert proposals == ()


# ---------------------------------------------------------------------------
# TC_FXH_005 — proposal notional
# ---------------------------------------------------------------------------


def test_proposal_notional_formula() -> None:
    hedger = FXHedger(
        policy=HedgePolicy(
            threshold_pct=Decimal("0.05"),
            target_hedge_ratio=Decimal("0.80"),
        )
    )
    proposals = hedger.propose_hedges(
        exposures={Currency.USD: Decimal("0.20")},
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    assert len(proposals) == 1
    proposal = proposals[0]
    # exposure_amount = household_equity × share = 100,000 × 0.20 = 20,000
    assert proposal.exposure_amount == _eur("20000.00")
    # hedged_notional = exposure × ratio = 20,000 × 0.80 = 16,000
    assert proposal.hedged_notional() == _eur("16000.0000")


def test_proposal_carries_decided_at_and_target_ratio() -> None:
    policy = HedgePolicy(
        threshold_pct=Decimal("0.05"),
        target_hedge_ratio=Decimal("0.65"),
    )
    proposals = FXHedger(policy=policy).propose_hedges(
        exposures={Currency.USD: Decimal("0.20")},
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    assert proposals[0].decided_at == _AT
    assert proposals[0].target_hedge_ratio == Decimal("0.65")


# ---------------------------------------------------------------------------
# TC_FXH_006 — deterministic Currency.value sort order
# ---------------------------------------------------------------------------


def test_proposals_are_sorted_alphabetically_by_currency() -> None:
    hedger = FXHedger(policy=HedgePolicy(threshold_pct=Decimal("0.05")))
    # Mix the iteration order — Python dicts preserve insertion order
    # but sort by Currency.value should be deterministic regardless.
    proposals = hedger.propose_hedges(
        exposures={
            Currency.USD: Decimal("0.20"),
            Currency.GBP: Decimal("0.10"),
            Currency.CHF: Decimal("0.08"),
        },
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    # Currency.value order: CHF, GBP, USD.
    currencies = [p.currency for p in proposals]
    assert currencies == [Currency.CHF, Currency.GBP, Currency.USD]


def test_proposal_tuple_replays_byte_identical() -> None:
    """REQ_NF_FXH_001 — identical inputs ⇒ identical proposal tuple."""
    hedger = FXHedger(policy=HedgePolicy())
    args = dict(
        exposures={Currency.USD: Decimal("0.20"), Currency.GBP: Decimal("0.10")},
        household_equity=_eur("100000"),
        base_currency=Currency.EUR,
        now=_AT,
    )
    a = hedger.propose_hedges(**args)  # type: ignore[arg-type]
    b = hedger.propose_hedges(**args)  # type: ignore[arg-type]
    assert a == b


def test_household_equity_currency_must_match_base() -> None:
    hedger = FXHedger(policy=HedgePolicy())
    import pytest as _pytest

    with _pytest.raises(ValueError, match="household_equity.currency"):
        hedger.propose_hedges(
            exposures={Currency.USD: Decimal("0.20")},
            household_equity=Money(Decimal("100000"), Currency.USD),
            base_currency=Currency.EUR,
            now=_AT,
        )
