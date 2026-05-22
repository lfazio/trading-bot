"""Currency-hedge P&L attribution drill — Phase 6 operational test.

End-to-end scenario walking the CR-011 FX-hedger Phase-5 flow:

  multi-currency portfolio → exposure aggregation →
  threshold-gated hedge proposals → forward open →
  FX rate movement → forward close → realised P&L + tax treatment

The drill builds a realistic multi-currency portfolio (EUR base,
USD / CHF / GBP exposures), drives one full hedge cycle per
currency, and asserts the documented P&L attribution + tax
treatment in REQ_F_FXH_005 / REQ_F_FXH_006.

REQ refs:
- REQ_F_FXH_002 — compute_fx_exposure aggregates per-currency
  shares against a base currency.
- REQ_F_FXH_003 — threshold-gated proposals (strict greater-than).
- REQ_F_FXH_005 — mark formula:
  ``notional × (current_fx_rate / entry_fx_rate - 1)``.
- REQ_F_FXH_006 — tax treatment: gains taxed at 30 % (France CTO
  PFU); losses pass through pre-tax.
- REQ_NF_FXH_001 — deterministic Currency.value ordering for
  proposal output.
- REQ_SDS_FXH_001 — separate ledger (no InstrumentClass.FX
  extension).
- REQ_SDS_FXH_002 — pure proposal computation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok
from trading_system.wealth_ops.fx_hedger import (
    FXHedger,
    FXHedgeLedger,
    HedgePolicy,
)
from trading_system.wealth_ops.fx_hedger.exposure import (
    MarkedPosition,
    compute_fx_exposure,
)
from trading_system.wealth_ops.fx_hedger.forward import (
    FXForwardState,
    HedgeProposal,
)


_BASE = Currency.EUR
_T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)


def _eur(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=_BASE)


def _pos(currency: Currency, value_in_base: str) -> MarkedPosition:
    return MarkedPosition(
        currency=currency, value_in_base=_eur(value_in_base)
    )


# ---------------------------------------------------------------------------
# Scenario 1 — exposure aggregation
# ---------------------------------------------------------------------------


def test_drill_exposure_aggregates_per_currency_share() -> None:
    """REQ_F_FXH_002 — exposure share = sum(value_in_base for
    currency) / household_equity. Base currency and zero-share
    currencies are omitted so consumers iterate over a tight
    non-empty mapping."""
    household_equity = _eur("100000")
    positions = [
        _pos(Currency.EUR, "30000"),  # base — omitted
        _pos(Currency.USD, "20000"),
        _pos(Currency.USD, "5000"),   # USD aggregated
        _pos(Currency.CHF, "12000"),
        _pos(Currency.GBP, "3000"),
    ]
    exposures = compute_fx_exposure(
        positions,
        base_currency=_BASE,
        household_equity=household_equity,
    )
    assert Currency.EUR not in exposures  # base omitted
    assert exposures[Currency.USD] == Decimal("0.25")  # 25k / 100k
    assert exposures[Currency.CHF] == Decimal("0.12")  # 12k / 100k
    assert exposures[Currency.GBP] == Decimal("0.03")  # 3k / 100k


# ---------------------------------------------------------------------------
# Scenario 2 — threshold-gated proposals
# ---------------------------------------------------------------------------


def test_drill_proposals_fire_only_above_threshold() -> None:
    """REQ_F_FXH_003 — strict greater-than at the threshold.
    Currencies exactly at the threshold SHALL NOT receive a
    proposal; only those strictly above do."""
    policy = HedgePolicy(
        threshold_pct=Decimal("0.05"),
        target_hedge_ratio=Decimal("0.80"),
    )
    hedger = FXHedger(policy=policy)
    exposures = {
        Currency.USD: Decimal("0.25"),  # well above 5 %
        Currency.CHF: Decimal("0.05"),  # exactly at threshold — omit
        Currency.GBP: Decimal("0.03"),  # below threshold — omit
    }
    household_equity = _eur("100000")
    proposals = hedger.propose_hedges(
        exposures,
        household_equity=household_equity,
        base_currency=_BASE,
        now=_T0,
    )
    assert len(proposals) == 1
    p = proposals[0]
    assert p.currency == Currency.USD
    assert p.exposure_amount == _eur("25000")
    # hedged notional = exposure × ratio = 25000 × 0.80 = 20000
    assert p.hedged_notional() == _eur("20000")


def test_drill_proposals_are_currency_value_sorted() -> None:
    """REQ_NF_FXH_001 — proposal output ordered by
    ``Currency.value`` so replay is deterministic. USD < CHF
    lexicographically? No — alphabetically: CHF < GBP < USD.
    The assertion verifies the documented ordering."""
    policy = HedgePolicy(
        threshold_pct=Decimal("0.05"),
        target_hedge_ratio=Decimal("1.0"),
    )
    hedger = FXHedger(policy=policy)
    exposures = {
        Currency.USD: Decimal("0.20"),
        Currency.CHF: Decimal("0.15"),
        Currency.GBP: Decimal("0.10"),
    }
    proposals = hedger.propose_hedges(
        exposures,
        household_equity=_eur("100000"),
        base_currency=_BASE,
        now=_T0,
    )
    currencies = [p.currency.value for p in proposals]
    assert currencies == sorted(currencies)


# ---------------------------------------------------------------------------
# Scenario 3 — open forward + mark-to-market formula
# ---------------------------------------------------------------------------


def test_drill_open_and_mark_to_market() -> None:
    """REQ_F_FXH_005 — mark formula:
    ``notional × (current_fx_rate / entry_fx_rate - 1)``.

    Open a USD forward at entry rate 1.10. After USD strengthens
    to 1.15 (current_rate higher → favourable for a long-EUR
    hedge), the gross P&L should be:
      20000 × (1.15 / 1.10 - 1) = 20000 × 0.04545... = ~909.09
    """
    ledger = FXHedgeLedger()
    proposal = HedgeProposal(
        currency=Currency.USD,
        base_currency=_BASE,
        exposure_amount=_eur("25000"),
        target_hedge_ratio=Decimal("0.80"),
        decided_at=_T0,
    )
    forward = ledger.open(
        proposal, entry_fx_rate=Decimal("1.10"), opened_at=_T0
    )
    assert forward.state is FXForwardState.OPEN
    assert forward.notional == _eur("20000")  # 25000 × 0.80

    close_result = ledger.close(
        forward.id, exit_fx_rate=Decimal("1.15"), closed_at=_T1
    )
    assert isinstance(close_result, Ok)
    pnl: Money = close_result.value
    # Formula: 20000 × (1.15/1.10 - 1) = 20000 × 0.04545454545...
    expected = Decimal("20000") * (
        Decimal("1.15") / Decimal("1.10") - Decimal(1)
    )
    assert pnl.amount == expected
    assert pnl.currency == _BASE
    assert pnl.amount > 0  # favourable move


def test_drill_unfavourable_move_produces_negative_pnl() -> None:
    """REQ_F_FXH_005 — when the FX rate moves against the hedge
    (exit < entry), gross P&L SHALL be negative."""
    ledger = FXHedgeLedger()
    proposal = HedgeProposal(
        currency=Currency.USD,
        base_currency=_BASE,
        exposure_amount=_eur("25000"),
        target_hedge_ratio=Decimal("0.80"),
        decided_at=_T0,
    )
    forward = ledger.open(
        proposal, entry_fx_rate=Decimal("1.10"), opened_at=_T0
    )
    close_result = ledger.close(
        forward.id, exit_fx_rate=Decimal("1.05"), closed_at=_T1
    )
    assert isinstance(close_result, Ok)
    pnl = close_result.value
    assert pnl.amount < 0


# ---------------------------------------------------------------------------
# Scenario 4 — close idempotence + not-found Errs
# ---------------------------------------------------------------------------


def test_drill_close_twice_returns_categorised_err() -> None:
    """REQ_SDD_FXH_004 — closing a CLOSED forward returns the
    categorised ``fxh:already_closed:<id>`` Err."""
    ledger = FXHedgeLedger()
    proposal = HedgeProposal(
        currency=Currency.CHF,
        base_currency=_BASE,
        exposure_amount=_eur("12000"),
        target_hedge_ratio=Decimal("0.80"),
        decided_at=_T0,
    )
    forward = ledger.open(
        proposal, entry_fx_rate=Decimal("0.95"), opened_at=_T0
    )
    first_close = ledger.close(
        forward.id, exit_fx_rate=Decimal("0.97"), closed_at=_T1
    )
    assert isinstance(first_close, Ok)
    second_close = ledger.close(
        forward.id, exit_fx_rate=Decimal("0.98"), closed_at=_T1
    )
    assert isinstance(second_close, Err)
    assert second_close.error.startswith("fxh:already_closed:")


def test_drill_close_unknown_id_returns_not_found() -> None:
    """REQ_SDD_FXH_004 — closing an unknown forward id returns
    ``fxh:not_found:<id>``."""
    from trading_system.wealth_ops.fx_hedger.forward import ForwardId

    ledger = FXHedgeLedger()
    result = ledger.close(
        ForwardId("fwd-ghost"),
        exit_fx_rate=Decimal("1.0"),
        closed_at=_T1,
    )
    assert isinstance(result, Err)
    assert result.error == "fxh:not_found:fwd-ghost"


# ---------------------------------------------------------------------------
# Scenario 5 — tax treatment (gains taxed, losses pass through)
# ---------------------------------------------------------------------------


def test_drill_realized_pnl_gross_sums_closed_forwards() -> None:
    """REQ_F_FXH_005 — ``realized_pnl_gross`` sums marks across
    every closed forward in the base currency.

    Setup: two closed forwards, +500 + (-200) = +300 gross.
    """
    ledger = FXHedgeLedger()
    # Forward 1 — gain.
    p1 = HedgeProposal(
        currency=Currency.USD,
        base_currency=_BASE,
        exposure_amount=_eur("10000"),
        target_hedge_ratio=Decimal("1.0"),
        decided_at=_T0,
    )
    f1 = ledger.open(p1, entry_fx_rate=Decimal("1.0"), opened_at=_T0)
    ledger.close(f1.id, exit_fx_rate=Decimal("1.05"), closed_at=_T1)
    # mark = 10000 × (1.05/1.0 - 1) = +500

    # Forward 2 — loss.
    p2 = HedgeProposal(
        currency=Currency.CHF,
        base_currency=_BASE,
        exposure_amount=_eur("10000"),
        target_hedge_ratio=Decimal("1.0"),
        decided_at=_T0,
    )
    f2 = ledger.open(p2, entry_fx_rate=Decimal("1.0"), opened_at=_T0)
    ledger.close(f2.id, exit_fx_rate=Decimal("0.98"), closed_at=_T1)
    # mark = 10000 × (0.98/1.0 - 1) = -200

    gross = ledger.realized_pnl_gross()
    assert gross.amount == Decimal("300.00")  # 500 + (-200)
    assert gross.currency == _BASE


def test_drill_net_positive_pnl_taxed_at_30pct() -> None:
    """REQ_F_FXH_006 — net-positive gross PnL is taxed at 30 %.
    Gross +1000 ⇒ after-tax 700."""
    ledger = FXHedgeLedger()
    proposal = HedgeProposal(
        currency=Currency.USD,
        base_currency=_BASE,
        exposure_amount=_eur("20000"),
        target_hedge_ratio=Decimal("1.0"),
        decided_at=_T0,
    )
    forward = ledger.open(
        proposal, entry_fx_rate=Decimal("1.0"), opened_at=_T0
    )
    # exit 1.05 → +5 % → +1000 gross
    ledger.close(forward.id, exit_fx_rate=Decimal("1.05"), closed_at=_T1)

    gross = ledger.realized_pnl_gross().amount
    after_tax = ledger.realized_pnl_after_tax().amount
    assert gross == Decimal("1000.00")
    assert after_tax == Decimal("700.00")  # 1000 × (1 - 0.30)
    assert after_tax < gross


def test_drill_net_negative_pnl_passes_through_pretax() -> None:
    """REQ_F_FXH_006 — losses pass through pre-tax (no
    "negative tax" credit). The operator's offset against gains
    happens elsewhere (via ``tax/harvest.py`` if the loss is
    realised on a position)."""
    ledger = FXHedgeLedger()
    proposal = HedgeProposal(
        currency=Currency.USD,
        base_currency=_BASE,
        exposure_amount=_eur("20000"),
        target_hedge_ratio=Decimal("1.0"),
        decided_at=_T0,
    )
    forward = ledger.open(
        proposal, entry_fx_rate=Decimal("1.0"), opened_at=_T0
    )
    # exit 0.95 → -5 % → -1000 gross
    ledger.close(forward.id, exit_fx_rate=Decimal("0.95"), closed_at=_T1)

    gross = ledger.realized_pnl_gross().amount
    after_tax = ledger.realized_pnl_after_tax().amount
    assert gross == Decimal("-1000.00")
    # Losses pass through unchanged.
    assert after_tax == gross


# ---------------------------------------------------------------------------
# Scenario 6 — full hedge cycle attribution
# ---------------------------------------------------------------------------


def test_drill_full_cycle_attribution_per_forward() -> None:
    """REQ_F_FXH_005 + REQ_NF_FXH_001 — drive a full hedge cycle
    over a multi-currency portfolio:

    1. Build positions (USD + CHF + GBP).
    2. Compute exposures.
    3. Generate proposals with a 5 % threshold + 80 % ratio.
    4. Open one forward per proposal.
    5. Move FX rates (USD favourable, CHF unfavourable, GBP flat).
    6. Close all forwards.
    7. Verify per-forward marks match the documented formula
       AND ``realized_pnl_gross`` equals their sum.
    8. Verify ``realized_pnl_after_tax`` applies the tax to the
       net-positive total.
    """
    household_equity = _eur("100000")
    positions = [
        _pos(Currency.USD, "25000"),
        _pos(Currency.CHF, "12000"),
        _pos(Currency.GBP, "10000"),
    ]
    exposures = compute_fx_exposure(
        positions,
        base_currency=_BASE,
        household_equity=household_equity,
    )
    # All three currencies above the 5 % threshold.
    assert exposures[Currency.USD] == Decimal("0.25")
    assert exposures[Currency.CHF] == Decimal("0.12")
    assert exposures[Currency.GBP] == Decimal("0.10")

    policy = HedgePolicy(
        threshold_pct=Decimal("0.05"),
        target_hedge_ratio=Decimal("0.80"),
    )
    hedger = FXHedger(policy=policy)
    proposals = hedger.propose_hedges(
        exposures,
        household_equity=household_equity,
        base_currency=_BASE,
        now=_T0,
    )
    assert len(proposals) == 3

    ledger = FXHedgeLedger()
    forwards_by_currency = {}
    for p in proposals:
        forwards_by_currency[p.currency] = ledger.open(
            p, entry_fx_rate=Decimal("1.0"), opened_at=_T0
        )

    # FX moves — same exit rates for clarity.
    moves = {
        Currency.USD: Decimal("1.05"),  # +5 % favourable
        Currency.CHF: Decimal("0.97"),  # -3 % unfavourable
        Currency.GBP: Decimal("1.00"),  # flat
    }
    closes_by_currency = {}
    for currency, forward in forwards_by_currency.items():
        result = ledger.close(
            forward.id,
            exit_fx_rate=moves[currency],
            closed_at=_T1,
        )
        assert isinstance(result, Ok)
        closes_by_currency[currency] = result.value.amount

    # Per-forward attribution (formula: notional × (exit/entry - 1)).
    # USD notional = 25000 × 0.80 = 20000 → mark = 20000 × 0.05 = +1000
    # CHF notional = 12000 × 0.80 = 9600  → mark = 9600 × -0.03 = -288
    # GBP notional = 10000 × 0.80 = 8000  → mark = 8000 × 0    = 0
    assert closes_by_currency[Currency.USD] == Decimal("1000.00")
    assert closes_by_currency[Currency.CHF] == Decimal("-288.00")
    assert closes_by_currency[Currency.GBP] == Decimal("0.00")

    # Aggregate gross + after-tax.
    gross_total = ledger.realized_pnl_gross().amount
    assert gross_total == Decimal("712.00")  # 1000 - 288 + 0
    after_tax_total = ledger.realized_pnl_after_tax().amount
    # net-positive 712 → after-tax 712 × 0.70 = 498.40
    assert after_tax_total == Decimal("498.40")
