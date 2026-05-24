"""C8 — Multi-account integration drill.

Operator hardening sprint slice. Exercises the CR-006 multi-account
surface end-to-end with a 3-account household:

  household = {alpha (1 000 EUR), beta (5 000 EUR), gamma (20 000 EUR)}

The drill validates every cross-account contract under realistic
load:

- :class:`AccountRegistry.list_accounts` returns accounts in
  lex-by-id order; :class:`AccountRegistry.tick` fans the pipeline
  out deterministically — same registry + same ``(now, pipeline)``
  produces the same sequence of `pipeline(account, now)` calls
  (REQ_NF_DET_001 / REQ_SDS_ACC_002 / REQ_SDD_ACC_002).
- :class:`PortfolioGroup` aggregates household equity + per-
  instrument exposure across the three accounts; FX-missing
  surfaces as a categorised ``accounts:fx_missing:<from>:<to>``
  Err (REQ_F_ACC_007 / REQ_SDD_ACC_004).
- :func:`cross_account_concentration_gate` short-circuits as a
  no-op for the single-account default (REQ_NF_ACC_001) and
  rejects a proposal whose projected household exposure would
  push the household-wide single-asset share above the cap
  (REQ_F_ACC_008 / REQ_SDS_ACC_004 / REQ_SDD_ACC_005); currency-
  mismatched exposures surface a distinct Err category.
- :class:`HouseholdDrawdownTrigger` emits ``Nothing`` below the
  degrade threshold, a ``DEGRADE`` :class:`KillSwitchTrigger` at
  the configured ``degrade_pct``, and a ``KILL`` trigger
  (pre-empting) at ``kill_pct`` — using the conservative
  max-across-accounts aggregation
  (REQ_F_ACC_009 / REQ_SDD_ACC_006).

The drill builds the household with hand-rolled fake portfolios
so the test stays decoupled from the live `Portfolio` runtime
type; the contract the production code relies on is the
duck-typed `equity() / positions_by_instrument() / drawdown_pct()`
accessor set, and the fakes satisfy each.

REQ refs: REQ_F_ACC_001..010, REQ_NF_ACC_001, REQ_SDS_ACC_002,
REQ_SDS_ACC_004, REQ_SDD_ACC_001, REQ_SDD_ACC_002, REQ_SDD_ACC_004,
REQ_SDD_ACC_005, REQ_SDD_ACC_006.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterator

from trading_system.accounts.account import Account
from trading_system.accounts.cross_account_risk import (
    cross_account_concentration_gate,
)
from trading_system.accounts.group import IdentityFxConverter, PortfolioGroup
from trading_system.accounts.household_drawdown_trigger import (
    HouseholdDrawdownTrigger,
)
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.models.identifiers import (
    AccountId,
    InstrumentId,
    SnapshotId,
    StrategyId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.meta import TradeProposal
from trading_system.models.money import Currency, Money
from trading_system.models.trading import Side, StopLoss
from trading_system.result import Err, Nothing, Ok, Some


# ---------------------------------------------------------------------------
# Test doubles — duck-typed against PortfolioGroup / drawdown trigger
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakePortfolio:
    """Minimal fake satisfying the duck-typed accessors that
    PortfolioGroup + HouseholdDrawdownTrigger consume."""

    equity_money: Money
    positions: dict[InstrumentId, Money] = field(default_factory=dict)
    drawdown: Decimal = Decimal("0")

    def equity(self) -> Money:
        return self.equity_money

    def positions_by_instrument(self) -> Iterator[tuple[InstrumentId, Money]]:
        yield from self.positions.items()

    def drawdown_pct(self) -> Decimal:
        return self.drawdown


def _account(
    account_id: str,
    *,
    equity_eur: Decimal,
    positions: dict[InstrumentId, Money] | None = None,
    drawdown: Decimal = Decimal("0"),
) -> Account:
    """Build an Account with a fake portfolio carrying the
    requested equity + position map + drawdown."""
    portfolio = _FakePortfolio(
        equity_money=Money(equity_eur, Currency.EUR),
        positions=positions or {},
        drawdown=drawdown,
    )
    return Account(
        id=AccountId(account_id),
        broker=None,
        portfolio=portfolio,
        capital_flow=None,
        phase_engine=None,
        tax_model=FranceCTOTaxModel(),
        risk_overlay=None,
        operator_token_account_id=account_id,
    )


def _household_3() -> tuple[AccountRegistry, dict[str, Account]]:
    """Standard 3-account household: alpha (1 k) + beta (5 k) +
    gamma (20 k). Total household equity = 26 000 EUR."""
    asml = InstrumentId("ASML.AS")
    air_pa = InstrumentId("AIR.PA")
    alpha = _account(
        "alpha",
        equity_eur=Decimal("1000"),
        positions={asml: Money(Decimal("200"), Currency.EUR)},
    )
    beta = _account(
        "beta",
        equity_eur=Decimal("5000"),
        positions={asml: Money(Decimal("800"), Currency.EUR)},
    )
    gamma = _account(
        "gamma",
        equity_eur=Decimal("20000"),
        positions={
            asml: Money(Decimal("2000"), Currency.EUR),
            air_pa: Money(Decimal("1500"), Currency.EUR),
        },
    )
    registry = AccountRegistry()
    for acct in (alpha, beta, gamma):
        result = registry.add(acct)
        assert isinstance(result, Ok), f"unexpected: {result}"
    return registry, {"alpha": alpha, "beta": beta, "gamma": gamma}


def _proposal(
    *,
    side: Side = Side.BUY,
    size_pct: Decimal = Decimal("0.05"),
    instrument_id: str = "ASML.AS",
) -> TradeProposal:
    stock = Stock(
        id=InstrumentId(instrument_id),
        symbol=instrument_id.split(".")[0],
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )
    return TradeProposal(
        instrument=stock,
        side=side,
        size_pct_of_capital=size_pct,
        expected_net_profit=Money(Decimal("100"), Currency.EUR),
        expected_fees=Money(Decimal("5"), Currency.EUR),
        stop_loss=StopLoss(Decimal("0.05")),
        source_strategy=StrategyId("test"),
    )


# ---------------------------------------------------------------------------
# Registry — deterministic fan-out across 3 accounts
# ---------------------------------------------------------------------------


def test_registry_lists_three_accounts_sorted_lexically() -> None:
    """REQ_NF_DET_001 / REQ_SDS_ACC_002 — list_accounts() returns
    accounts in alphabetical order regardless of insertion order."""
    registry, _ = _household_3()
    ids = [str(a.id) for a in registry.list_accounts()]
    assert ids == ["alpha", "beta", "gamma"]


def test_registry_rejects_duplicate_id() -> None:
    """Defensive — the registry is the single insertion point; a
    duplicate id surfaces as ``accounts:duplicate_id:<id>``."""
    registry, _ = _household_3()
    dup = _account("beta", equity_eur=Decimal("999"))
    result = registry.add(dup)
    assert isinstance(result, Err)
    assert result.error == "accounts:duplicate_id:beta"


def test_registry_tick_fans_pipeline_deterministically() -> None:
    """REQ_SDD_ACC_002 — same registry + (now, pipeline) ⇒ same
    sequence of pipeline(account, now) invocations."""
    registry, _ = _household_3()
    seen_a: list[str] = []
    seen_b: list[str] = []
    now = datetime(2026, 5, 23, 12, tzinfo=UTC)
    registry.tick(now, lambda acct, _: seen_a.append(str(acct.id)))
    registry.tick(now, lambda acct, _: seen_b.append(str(acct.id)))
    assert seen_a == seen_b == ["alpha", "beta", "gamma"]


def test_registry_is_single_account_false_with_three() -> None:
    """REQ_NF_ACC_001 short-circuit guard — multi-account
    registries are NOT single-account."""
    registry, _ = _household_3()
    assert not registry.is_single_account()
    assert registry.size() == 3


# ---------------------------------------------------------------------------
# PortfolioGroup — household equity + per-instrument aggregation
# ---------------------------------------------------------------------------


def test_household_equity_sums_three_accounts() -> None:
    """REQ_F_ACC_007 / REQ_SDD_ACC_004 — household_equity is the
    sum of every account's after-tax equity, normalised to base
    currency."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    result = group.household_equity()
    assert isinstance(result, Ok)
    assert result.value == Money(Decimal("26000"), Currency.EUR)


def test_household_exposure_aggregates_by_instrument() -> None:
    """Per-instrument exposure aggregates across accounts; the
    ASML position lives in all three (200 + 800 + 2000 = 3000 EUR)
    + AIR.PA only in gamma (1500 EUR)."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    result = group.exposure_by_instrument()
    assert isinstance(result, Ok)
    expo = result.value
    assert expo[InstrumentId("ASML.AS")] == Money(
        Decimal("3000"), Currency.EUR
    )
    assert expo[InstrumentId("AIR.PA")] == Money(
        Decimal("1500"), Currency.EUR
    )


def test_household_equity_surfaces_fx_miss() -> None:
    """REQ_SDD_ACC_004 — when an account is in a non-base
    currency and no FX converter is wired, the aggregator
    SHALL surface a categorised ``accounts:fx_missing:<from>:<to>``
    Err rather than silently dropping the account's contribution."""
    registry = AccountRegistry()
    registry.add(_account("alpha", equity_eur=Decimal("1000")))
    # Inject a USD-equity account into the household — IdentityFxConverter
    # rejects any currency != base.
    usd_portfolio = _FakePortfolio(
        equity_money=Money(Decimal("500"), Currency.USD)
    )
    usd_account = Account(
        id=AccountId("usd-account"),
        broker=None,
        portfolio=usd_portfolio,
        capital_flow=None,
        phase_engine=None,
        tax_model=FranceCTOTaxModel(),
        risk_overlay=None,
        operator_token_account_id="usd-account",
    )
    registry.add(usd_account)
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    result = group.household_equity()
    assert isinstance(result, Err)
    assert result.error == "accounts:fx_missing:USD:EUR"


# ---------------------------------------------------------------------------
# Cross-account concentration gate
# ---------------------------------------------------------------------------


def test_concentration_gate_noop_in_single_account_deployment() -> None:
    """REQ_NF_ACC_001 — the gate SHALL short-circuit as a no-op
    when the registry holds exactly one account, regardless of
    proposal shape. The per-account RiskEngine.pre_trade single-
    asset cap (REQ_F_RSK_002) already covers that case."""
    registry = AccountRegistry()
    registry.add(_account("default", equity_eur=Decimal("10000")))
    # A 100% proposal that would catastrophically breach any cap
    # is still Ok because the registry is single-account.
    result = cross_account_concentration_gate(
        _proposal(size_pct=Decimal("1.0")),
        registry=registry,
        household_exposure={},
        household_equity=Money(Decimal("10000"), Currency.EUR),
        cap_pct=Decimal("0.02"),
    )
    assert isinstance(result, Ok)


def test_concentration_gate_passes_under_cap() -> None:
    """REQ_F_ACC_008 — multi-account; existing ASML exposure
    3 000 / 26 000 = 11.5%; adding a 2% (520 EUR) buy → projected
    13.5% < cap_pct=15%. Passes."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    equity = group.household_equity().unwrap()
    expo = group.exposure_by_instrument().unwrap()
    result = cross_account_concentration_gate(
        _proposal(side=Side.BUY, size_pct=Decimal("0.02")),
        registry=registry,
        household_exposure=expo,
        household_equity=equity,
        cap_pct=Decimal("0.15"),
    )
    assert isinstance(result, Ok)


def test_concentration_gate_rejects_when_proposal_pushes_over_cap() -> None:
    """REQ_F_ACC_008 / REQ_SDD_ACC_005 — projected ASML share
    after a BUY that adds 1 300 EUR (5% of 26 000) crosses
    cap_pct=15%: existing 3 000 + 1 300 = 4 300 / 26 000 ≈ 16.5%.
    Gate rejects with a categorised ``risk:cross_account_
    concentration:<instrument>`` Err."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    equity = group.household_equity().unwrap()
    expo = group.exposure_by_instrument().unwrap()
    result = cross_account_concentration_gate(
        _proposal(side=Side.BUY, size_pct=Decimal("0.05")),
        registry=registry,
        household_exposure=expo,
        household_equity=equity,
        cap_pct=Decimal("0.15"),
    )
    assert isinstance(result, Err)
    assert result.error == "risk:cross_account_concentration:ASML.AS"


def test_concentration_gate_sell_reduces_projected_share() -> None:
    """A SELL contributes a negative delta — the projected share
    SHALL move towards zero, not toward the cap. With existing
    3 000 EUR ASML exposure and a 10% (2 600 EUR) SELL, projected
    is 400 EUR ≈ 1.5%."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    equity = group.household_equity().unwrap()
    expo = group.exposure_by_instrument().unwrap()
    result = cross_account_concentration_gate(
        _proposal(side=Side.SELL, size_pct=Decimal("0.10")),
        registry=registry,
        household_exposure=expo,
        household_equity=equity,
        cap_pct=Decimal("0.05"),
    )
    assert isinstance(result, Ok)


def test_concentration_gate_currency_mismatch_surfaces_err() -> None:
    """If a household-exposure entry's currency differs from the
    household_equity's, the gate SHALL surface a categorised Err
    rather than silently summing incompatible amounts."""
    registry, _ = _household_3()
    bad_exposure = {
        InstrumentId("ASML.AS"): Money(Decimal("1000"), Currency.USD),
    }
    result = cross_account_concentration_gate(
        _proposal(),
        registry=registry,
        household_exposure=bad_exposure,
        household_equity=Money(Decimal("26000"), Currency.EUR),
        cap_pct=Decimal("0.15"),
    )
    assert isinstance(result, Err)
    assert "currency_mismatch" in result.error


def test_concentration_gate_rejects_bad_cap_pct() -> None:
    """``cap_pct`` SHALL lie in (0, 1]. Defensive guard against
    a config mis-load handing a zero / negative / > 1 cap."""
    registry, _ = _household_3()
    for bad in (Decimal("0"), Decimal("-0.5"), Decimal("1.5")):
        result = cross_account_concentration_gate(
            _proposal(),
            registry=registry,
            household_exposure={},
            household_equity=Money(Decimal("26000"), Currency.EUR),
            cap_pct=bad,
        )
        assert isinstance(result, Err)
        assert "bad_cap_pct" in result.error


def test_concentration_gate_rejects_zero_household_equity() -> None:
    """Zero / negative household equity ⇒ a division-by-zero
    landmine. The gate SHALL surface a categorised Err so the
    caller fails fast instead of crashing."""
    registry, _ = _household_3()
    result = cross_account_concentration_gate(
        _proposal(),
        registry=registry,
        household_exposure={},
        household_equity=Money(Decimal("0"), Currency.EUR),
        cap_pct=Decimal("0.15"),
    )
    assert isinstance(result, Err)
    assert "zero_equity" in result.error


# ---------------------------------------------------------------------------
# Household drawdown trigger — DEGRADE pre-empted by KILL on same tick
# ---------------------------------------------------------------------------


_AT = datetime(2026, 5, 23, 12, tzinfo=UTC)
_SNAPSHOT = SnapshotId("ks/snap-001")


def _trigger(registry: AccountRegistry, *, kill_pct: Decimal = Decimal("0.15")) -> HouseholdDrawdownTrigger:
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    return HouseholdDrawdownTrigger(
        group=group,
        degrade_pct=Decimal("0.12"),
        kill_pct=kill_pct,
    )


def test_household_drawdown_below_degrade_emits_nothing() -> None:
    """All three accounts at < 12% drawdown ⇒ household max < 12%
    ⇒ trigger emits ``Nothing``."""
    registry, _ = _household_3()
    # Drawdowns: alpha 0.05, beta 0.08, gamma 0.10 ⇒ max 0.10 < 0.12.
    accounts = registry.list_accounts()
    accounts[0].portfolio.drawdown = Decimal("0.05")
    accounts[1].portfolio.drawdown = Decimal("0.08")
    accounts[2].portfolio.drawdown = Decimal("0.10")
    trigger = _trigger(registry)
    result = trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT)
    assert isinstance(result, Ok)
    assert isinstance(result.value, Nothing)


def test_household_drawdown_at_degrade_threshold_emits_degrade() -> None:
    """Any account hitting 12% pushes household max to 12% ⇒
    DEGRADE trigger emitted."""
    registry, _ = _household_3()
    accounts = registry.list_accounts()
    accounts[1].portfolio.drawdown = Decimal("0.12")
    trigger = _trigger(registry)
    result = trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT)
    assert isinstance(result, Ok)
    assert isinstance(result.value, Some)
    ks = result.value.value
    assert ks.severity == "DEGRADE"
    assert ks.code == "financial:household_drawdown:degrade"
    assert ks.snapshot_id == _SNAPSHOT


def test_household_drawdown_at_kill_threshold_emits_kill_preempting_degrade() -> None:
    """A 15% account drawdown SHALL emit a KILL trigger; the
    severity ordering KILL > DEGRADE means a tick at the kill
    threshold emits KILL even though DEGRADE is also breached."""
    registry, _ = _household_3()
    accounts = registry.list_accounts()
    accounts[2].portfolio.drawdown = Decimal("0.15")
    trigger = _trigger(registry)
    result = trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT)
    assert isinstance(result, Ok)
    assert isinstance(result.value, Some)
    ks = result.value.value
    assert ks.severity == "KILL"
    assert ks.code == "financial:household_drawdown:kill"


def test_household_drawdown_uses_max_across_accounts() -> None:
    """REQ_SDD_ACC_006 — v1 aggregator is max across accounts.
    Even if two accounts are well below the threshold, a third
    hitting kill triggers the household alert."""
    registry, _ = _household_3()
    accounts = registry.list_accounts()
    accounts[0].portfolio.drawdown = Decimal("0.01")
    accounts[1].portfolio.drawdown = Decimal("0.02")
    accounts[2].portfolio.drawdown = Decimal("0.20")  # over kill
    trigger = _trigger(registry)
    result = trigger.evaluate(at=_AT, snapshot_id=_SNAPSHOT)
    assert isinstance(result, Ok)
    assert isinstance(result.value, Some)
    assert result.value.value.severity == "KILL"


def test_household_drawdown_trigger_rejects_inverted_thresholds() -> None:
    """Construction-time invariant: degrade_pct < kill_pct.
    Catches a config mis-load."""
    import pytest

    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    with pytest.raises(ValueError, match="degrade_pct"):
        HouseholdDrawdownTrigger(
            group=group,
            degrade_pct=Decimal("0.15"),
            kill_pct=Decimal("0.10"),
        )


# ---------------------------------------------------------------------------
# End-to-end: deterministic tick fan-out + household snapshot drift
# ---------------------------------------------------------------------------


def test_tick_then_household_snapshot_is_deterministic() -> None:
    """REQ_SDD_ACC_002 — running two identical ticks against the
    household SHALL produce identical observed-state sequences.
    The household snapshot reads through PortfolioGroup AFTER the
    per-account loop, so cross-account state inside the tick is
    the previous tick's snapshot (REQ_SDS_ACC_002)."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )

    def _run_one_tick() -> tuple[list[str], Money]:
        observed: list[str] = []
        registry.tick(_AT, lambda acct, _: observed.append(str(acct.id)))
        equity = group.household_equity().unwrap()
        return observed, equity

    seq_a, equity_a = _run_one_tick()
    seq_b, equity_b = _run_one_tick()
    assert seq_a == seq_b == ["alpha", "beta", "gamma"]
    assert equity_a == equity_b == Money(Decimal("26000"), Currency.EUR)


def test_concentration_gate_uses_live_aggregated_exposure() -> None:
    """End-to-end — pull household exposure from PortfolioGroup
    (not a hand-rolled mapping), pass it through the gate. Proves
    the wiring path the production code follows."""
    registry, _ = _household_3()
    group = PortfolioGroup(
        registry=registry,
        base_currency=Currency.EUR,
        fx=IdentityFxConverter(),
    )
    # Build a proposal in AIR.PA — currently only gamma holds it
    # (1 500 EUR ≈ 5.8% household share). A 1% BUY (260 EUR) keeps
    # us under a 15% cap easily.
    proposal = _proposal(
        instrument_id="AIR.PA", side=Side.BUY, size_pct=Decimal("0.01")
    )
    result = cross_account_concentration_gate(
        proposal,
        registry=registry,
        household_exposure=group.exposure_by_instrument().unwrap(),
        household_equity=group.household_equity().unwrap(),
        cap_pct=Decimal("0.15"),
    )
    assert isinstance(result, Ok)
