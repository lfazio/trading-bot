"""Tests for ``trading_system.milestone_controller.controller``.

Covers TC_MIL_001..004:
- TC_MIL_001 — default milestone list shape.
- TC_MIL_002 — crossing requires every gate (stable + low_dd +
  consistent + no recent KS + not fake-growth).
- TC_MIL_003 — exposure unlock capped at [10%, 20%]; no exponential
  scaling representable.
- TC_MIL_004 — fake-growth detector trips on any of: 30d gain
  > 30%, single trade > 50%, vol > 2x rolling.

REQ refs:
- REQ_F_MIL_001 — default milestone list shape.
- REQ_F_MIL_002 — every gating condition required for crossing.
- REQ_F_MIL_003 — gradual scaling band [10%, 20%].
- REQ_F_MIL_004 — fake-growth detector.
- REQ_SDS_MOD_012 — fake-growth detector belongs here.
- REQ_SDD_ALG_015 — concrete fake-growth thresholds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.capital_flow.flow import CapitalFlow
from trading_system.milestone_controller import (
    DEFAULT_MILESTONES,
    MilestoneConfig,
    MilestoneController,
    MilestoneCrossing,
    PerformanceMetrics,
)
from trading_system.models.identifiers import SnapshotId
from trading_system.models.money import Currency, Money
from trading_system.models.safety import KillSwitchTrigger, TriggerCategory
from trading_system.result import Nothing, Some

EUR = Currency.EUR


def _eur(x: str) -> Money:
    return Money(Decimal(x), EUR)


def _ts() -> datetime:
    return datetime(2026, 5, 8, tzinfo=UTC)


def _perf(  # noqa: PLR0913 — test helper; mirrors PerformanceMetrics fields
    *,
    stable: bool = True,
    low_dd: bool = True,
    consistent: bool = True,
    gain_30d: str = "0.05",
    largest_trade_pct: str = "0.10",
    realized_vol: str = "0.15",
    rolling_vol_avg: str = "0.15",
) -> PerformanceMetrics:
    return PerformanceMetrics(
        stable_returns=stable,
        low_drawdown=low_dd,
        strategy_consistency=consistent,
        gain_30d=Decimal(gain_30d),
        largest_trade_pct=Decimal(largest_trade_pct),
        realized_vol=Decimal(realized_vol),
        rolling_vol_avg=Decimal(rolling_vol_avg),
    )


def _ks_trigger() -> KillSwitchTrigger:
    return KillSwitchTrigger(
        category=TriggerCategory.FINANCIAL,
        code="dd_breach",
        message="test",
        severity="DEGRADE",
        raised_at=_ts(),
        snapshot_id=SnapshotId("snap-1"),
    )


# ---------------------------------------------------------------------------
# TC_MIL_001 — default milestone list shape
# ---------------------------------------------------------------------------


def test_default_milestone_list_matches_spec() -> None:
    expected = [
        Decimal(2_000),
        Decimal(5_000),
        Decimal(10_000),
        Decimal(20_000),
        Decimal(50_000),
        Decimal(100_000),
        Decimal(200_000),
        Decimal(500_000),
        Decimal(1_000_000),
        Decimal(2_000_000),
        Decimal(5_000_000),
    ]
    assert [m.amount for m in DEFAULT_MILESTONES] == expected


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_milestones_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            MilestoneController(milestones=())

    def test_non_ascending_milestones_rejected(self) -> None:
        with pytest.raises(ValueError, match="strictly ascending"):
            MilestoneController(milestones=(_eur("1000"), _eur("500")))

    def test_mixed_currency_rejected(self) -> None:
        with pytest.raises(ValueError, match="must share a currency"):
            MilestoneController(milestones=(_eur("1000"), Money(Decimal("2000"), Currency.USD)))

    def test_zero_milestone_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            MilestoneController(milestones=(_eur("0"), _eur("1000")))


# ---------------------------------------------------------------------------
# TC_MIL_002 — every gate required
# ---------------------------------------------------------------------------


class TestGating:
    def test_below_first_milestone_returns_nothing(self) -> None:
        c = MilestoneController(milestones=(_eur("2000"),))
        cf = CapitalFlow(initial=_eur("1000"))
        result = c.evaluate(_eur("1500"), cf, recent_kill_switch_triggers=(), perf=_perf())
        assert result == Nothing()

    def test_all_gates_pass_emits_crossing(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        result = c.evaluate(
            _eur("2500"),
            cf,
            recent_kill_switch_triggers=(),
            perf=_perf(),
        )
        match result:
            case Some(crossing):
                assert crossing.target == _eur("2000")
                assert crossing.exposure_increase_pct == Decimal("0.10")
            case Nothing():
                raise AssertionError("expected Some(crossing)")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"stable": False},
            {"low_dd": False},
            {"consistent": False},
        ],
    )
    def test_failed_gating_boolean_blocks(self, kwargs: dict) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(**kwargs),
            )
            == Nothing()
        )

    def test_recent_ks_blocks(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(_ks_trigger(),),
                perf=_perf(),
            )
            == Nothing()
        )

    def test_currency_mismatch_panics(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        with pytest.raises(ValueError, match="must match milestone currency"):
            c.evaluate(
                Money(Decimal("2500"), Currency.USD),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(),
            )


# ---------------------------------------------------------------------------
# TC_MIL_003 — exposure cap at [10%, 20%]
# ---------------------------------------------------------------------------


class TestExposureCap:
    def test_default_unlock_is_10_percent(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        match c.evaluate(_eur("2500"), cf, recent_kill_switch_triggers=(), perf=_perf()):
            case Some(crossing):
                assert crossing.exposure_increase_pct == Decimal("0.10")
            case Nothing():
                raise AssertionError("expected Some")

    def test_configured_unlock_within_band(self) -> None:
        c = MilestoneController(cfg=MilestoneConfig(exposure_increase_pct=Decimal("0.20")))
        cf = CapitalFlow(initial=_eur("1000"))
        match c.evaluate(_eur("2500"), cf, recent_kill_switch_triggers=(), perf=_perf()):
            case Some(crossing):
                assert crossing.exposure_increase_pct == Decimal("0.20")
            case Nothing():
                raise AssertionError("expected Some")

    @pytest.mark.parametrize("pct", ["0.05", "0.25", "1.0"])
    def test_outside_band_rejected_by_config(self, pct: str) -> None:
        with pytest.raises(ValueError, match="must lie in"):
            MilestoneConfig(exposure_increase_pct=Decimal(pct))

    @pytest.mark.parametrize("pct", ["0.05", "0.30", "2.0"])
    def test_outside_band_rejected_by_crossing_constructor(self, pct: str) -> None:
        with pytest.raises(ValueError, match="must lie in"):
            MilestoneCrossing(target=_eur("2000"), exposure_increase_pct=Decimal(pct))


# ---------------------------------------------------------------------------
# TC_MIL_004 — fake-growth detector (REQ_SDD_ALG_015)
# ---------------------------------------------------------------------------


class TestFakeGrowth:
    def test_30d_gain_above_30_percent_blocks(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(gain_30d="0.31"),
            )
            == Nothing()
        )

    def test_30d_gain_at_threshold_passes(self) -> None:
        # Strict >: 30% exactly does NOT trip.
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        result = c.evaluate(
            _eur("2500"), cf, recent_kill_switch_triggers=(), perf=_perf(gain_30d="0.30")
        )
        assert isinstance(result, Some)

    def test_largest_trade_above_50_percent_blocks(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(largest_trade_pct="0.51"),
            )
            == Nothing()
        )

    def test_realized_vol_above_two_x_rolling_blocks(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(realized_vol="0.31", rolling_vol_avg="0.15"),
            )
            == Nothing()
        )

    def test_zero_rolling_vol_blocks_when_realized_vol_positive(self) -> None:
        # Edge: rolling vol == 0 (cold start) -> any positive realized
        # vol is treated as fake-growth (conservative).
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        assert (
            c.evaluate(
                _eur("2500"),
                cf,
                recent_kill_switch_triggers=(),
                perf=_perf(realized_vol="0.01", rolling_vol_avg="0"),
            )
            == Nothing()
        )


# ---------------------------------------------------------------------------
# Single-shot semantics — register_crossed advances the ladder
# ---------------------------------------------------------------------------


class TestSingleShot:
    def test_register_then_evaluate_emits_next_milestone(self) -> None:
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("1000"))
        match c.evaluate(_eur("12000"), cf, recent_kill_switch_triggers=(), perf=_perf()):
            case Some(first):
                assert first.target == _eur("2000")
            case Nothing():
                raise AssertionError("expected Some")
        c.register_crossed(_eur("2000"))
        match c.evaluate(_eur("12000"), cf, recent_kill_switch_triggers=(), perf=_perf()):
            case Some(second):
                assert second.target == _eur("5000")
            case Nothing():
                raise AssertionError("expected Some")

    def test_milestones_below_initial_are_auto_marked(self) -> None:
        # Operator starts with 5000 EUR; the 2k and 5k milestones
        # are below the floor and SHOULD NOT fire — the controller
        # treats them as already-crossed.
        c = MilestoneController()
        cf = CapitalFlow(initial=_eur("5000"))
        match c.evaluate(_eur("11000"), cf, recent_kill_switch_triggers=(), perf=_perf()):
            case Some(crossing):
                # 2k and 5k auto-skipped; first eligible is 10k.
                assert crossing.target == _eur("10000")
            case Nothing():
                raise AssertionError("expected Some")
        # Crossed view shows the auto-skipped levels.
        crossed_amounts = {m.amount for m in c.crossed}
        assert Decimal(2_000) in crossed_amounts
        assert Decimal(5_000) in crossed_amounts
