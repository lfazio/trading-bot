"""Tests for ``trading_system.backtesting.config.BacktestConfig``.

Phase-8 C1 coverage cleanup. Targets the invariant validators in
``BacktestConfig.__post_init__`` (start < end, positive starting
capital, non-negative spread, injection-schedule currency +
window). The existing engine + walk-forward tests exercise the
happy path; this file pins the Err branches so the constructor's
invariant set stays a wall against config typos.

REQ refs: REQ_SDS_ARC_005, REQ_F_BCT_001, REQ_NF_DET_001,
REQ_F_BCT_007 (injection schedule), REQ_SDD_ALG_019.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.backtesting.config import BacktestConfig
from trading_system.data.types import Timeframe
from trading_system.models.flow import Injection
from trading_system.models.money import Currency, Money
from trading_system.tax.config import TaxConfig


_EUR = Currency.EUR
_TAX = TaxConfig.default()


def _ok_kwargs() -> dict:
    return {
        "seed": 42,
        "start": datetime(2026, 1, 1, tzinfo=UTC),
        "end": datetime(2026, 4, 1, tzinfo=UTC),
        "timeframe": Timeframe.D1,
        "starting_capital": Money(Decimal("10000"), _EUR),
        "tax": _TAX,
    }


# ---------------------------------------------------------------------------
# Happy path — sanity
# ---------------------------------------------------------------------------


def test_minimal_valid_config_constructs() -> None:
    cfg = BacktestConfig(**_ok_kwargs())
    assert cfg.start < cfg.end
    assert cfg.starting_capital.amount == Decimal("10000")
    assert cfg.injection_schedule == ()
    assert cfg.spread_pct == Decimal("0")


def test_config_with_injection_schedule_constructs() -> None:
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(
            at=datetime(2026, 2, 1, tzinfo=UTC),
            amount=Money(Decimal("500"), _EUR),
        ),
        Injection(
            at=datetime(2026, 3, 1, tzinfo=UTC),
            amount=Money(Decimal("750"), _EUR),
        ),
    )
    cfg = BacktestConfig(**kwargs)
    assert len(cfg.injection_schedule) == 2


# ---------------------------------------------------------------------------
# Err branches — REQ_F_BCT_001 + REQ_F_BCT_007
# ---------------------------------------------------------------------------


def test_start_equals_end_rejected() -> None:
    kwargs = _ok_kwargs()
    kwargs["end"] = kwargs["start"]
    with pytest.raises(ValueError, match=r"start.*must be < end"):
        BacktestConfig(**kwargs)


def test_start_after_end_rejected() -> None:
    kwargs = _ok_kwargs()
    kwargs["start"] = datetime(2026, 5, 1, tzinfo=UTC)
    kwargs["end"] = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match=r"start.*must be < end"):
        BacktestConfig(**kwargs)


def test_zero_starting_capital_rejected() -> None:
    kwargs = _ok_kwargs()
    kwargs["starting_capital"] = Money(Decimal("0"), _EUR)
    with pytest.raises(ValueError, match="starting_capital must be > 0"):
        BacktestConfig(**kwargs)


def test_negative_starting_capital_rejected() -> None:
    kwargs = _ok_kwargs()
    kwargs["starting_capital"] = Money(Decimal("-100"), _EUR)
    with pytest.raises(ValueError, match="starting_capital must be > 0"):
        BacktestConfig(**kwargs)


def test_negative_spread_pct_rejected() -> None:
    kwargs = _ok_kwargs()
    kwargs["spread_pct"] = Decimal("-0.001")
    with pytest.raises(ValueError, match="spread_pct must be >= 0"):
        BacktestConfig(**kwargs)


def test_zero_spread_pct_accepted() -> None:
    """Zero spread is the v1 default — the boundary is inclusive."""
    kwargs = _ok_kwargs()
    kwargs["spread_pct"] = Decimal("0")
    cfg = BacktestConfig(**kwargs)
    assert cfg.spread_pct == Decimal("0")


def test_injection_currency_mismatch_rejected() -> None:
    """REQ_F_BCT_007 — every injection's currency SHALL match the
    starting-capital currency. Mixed-currency injections would
    silently misrepresent the capital flow."""
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(
            at=datetime(2026, 2, 1, tzinfo=UTC),
            amount=Money(Decimal("500"), Currency.USD),
        ),
    )
    with pytest.raises(
        ValueError,
        match="injection_schedule currency must match",
    ):
        BacktestConfig(**kwargs)


def test_injection_before_start_rejected() -> None:
    """An injection scheduled BEFORE ``start`` would either silently
    drop (data loss) or back-date the capital flow (replay-
    determinism violation). The constructor rejects."""
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(
            at=datetime(2025, 12, 1, tzinfo=UTC),
            amount=Money(Decimal("500"), _EUR),
        ),
    )
    with pytest.raises(
        ValueError, match=r"injection_schedule.*outside"
    ):
        BacktestConfig(**kwargs)


def test_injection_after_end_rejected() -> None:
    """Symmetric — an injection past ``end`` SHALL be rejected."""
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(
            at=datetime(2026, 6, 1, tzinfo=UTC),
            amount=Money(Decimal("500"), _EUR),
        ),
    )
    with pytest.raises(
        ValueError, match=r"injection_schedule.*outside"
    ):
        BacktestConfig(**kwargs)


def test_injection_at_start_boundary_accepted() -> None:
    """The window is inclusive: an injection AT ``start`` is fine."""
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(at=kwargs["start"], amount=Money(Decimal("500"), _EUR)),
    )
    cfg = BacktestConfig(**kwargs)
    assert cfg.injection_schedule[0].at == kwargs["start"]


def test_injection_at_end_boundary_accepted() -> None:
    """The window is inclusive at end too."""
    kwargs = _ok_kwargs()
    kwargs["injection_schedule"] = (
        Injection(at=kwargs["end"], amount=Money(Decimal("500"), _EUR)),
    )
    cfg = BacktestConfig(**kwargs)
    assert cfg.injection_schedule[0].at == kwargs["end"]


# ---------------------------------------------------------------------------
# Frozen-dataclass invariant — no runtime mutation
# ---------------------------------------------------------------------------


def test_config_is_frozen() -> None:
    """REQ_SDS_INT_004 family — config records are frozen so a
    runtime mutation surfaces immediately."""
    from dataclasses import FrozenInstanceError

    cfg = BacktestConfig(**_ok_kwargs())
    with pytest.raises(FrozenInstanceError):
        cfg.seed = 0  # type: ignore[misc]
