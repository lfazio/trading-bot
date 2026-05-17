"""TC_HOV_008 + TC_HOV_009 — ``OverlayLedger``.

REQ refs:
- REQ_F_HOV_005 — append-only ledger; deterministic mark + carry.
- REQ_SDD_HOV_004 — exact formulas, no rounding.
- REQ_C_TAX_001 — gains × 0.70; losses pass through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_system.institutional.hedge_overlay import (
    OverlayLedger,
    OverlayPolicy,
    OverlayPositionState,
)
from trading_system.result import Err


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# TC_HOV_008 — open / close / mark / carry happy path
# ---------------------------------------------------------------------------


def test_open_records_position() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    assert pos.id == 1
    assert pos.state is OverlayPositionState.OPEN
    assert led.positions() == (pos,)


def test_open_assigns_monotonic_ids() -> None:
    led = OverlayLedger()
    a = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    b = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("50000"),
        entry_index_level=Decimal("4520"),
        at=_T0 + timedelta(days=1),
    )
    assert (a.id, b.id) == (1, 2)


def test_mark_formula_exact() -> None:
    """REQ_SDD_HOV_004 — ``notional × (current/entry - 1)`` exact."""
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    # 100_000 × (4725/4500 - 1) = 100_000 × 0.05 = 5000
    assert led.mark(position=pos, current_index_level=Decimal("4725")) == Decimal(
        "5000"
    )


def test_carry_cost_formula_exact() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    policy = OverlayPolicy()  # carry_pct_per_year=0.005
    # 100_000 × 0.005 × (7 / 365) = 500 × (7/365) ≈ 9.58904109589041...
    cost = led.carry_cost(position=pos, elapsed_days=7, policy=policy)
    expected = Decimal("100000") * Decimal("0.005") * (Decimal("7") / Decimal("365"))
    assert cost == expected


def test_close_records_exit() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    closed_res = led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4725"),
        at=_T0 + timedelta(days=7),
    )
    closed = closed_res.unwrap()
    assert closed.state is OverlayPositionState.CLOSED
    assert closed.exit_index_level == Decimal("4725")
    # The stored position is the closed row.
    assert led.positions()[0].state is OverlayPositionState.CLOSED


def test_close_not_found_returns_err() -> None:
    led = OverlayLedger()
    match led.close(
        position_id=99,
        exit_index_level=Decimal("4500"),
        at=_T0,
    ):
        case Err(reason):
            assert reason.category == "hov:not_found:99"
        case _:
            raise AssertionError("expected Err on missing id")


def test_close_already_closed_returns_err() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4725"),
        at=_T0 + timedelta(days=7),
    ).unwrap()
    match led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4800"),
        at=_T0 + timedelta(days=14),
    ):
        case Err(reason):
            assert reason.category.startswith("hov:already_closed")
        case _:
            raise AssertionError("expected Err on already-closed")


def test_realized_gross_sums_closed_positions() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4725"),
        at=_T0 + timedelta(days=7),
    ).unwrap()
    assert led.realized_pnl_gross() == Decimal("5000")


# ---------------------------------------------------------------------------
# TC_HOV_009 — tax treatment
# ---------------------------------------------------------------------------


def test_realized_after_tax_applies_30pct_on_gain() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4725"),  # gain = 5000
        at=_T0 + timedelta(days=7),
    ).unwrap()
    # 5000 × 0.70 = 3500
    assert led.realized_pnl_after_tax() == Decimal("3500.00")


def test_realized_after_tax_passes_loss_through() -> None:
    led = OverlayLedger()
    pos = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    # exit 4275 ⇒ pnl = 100_000 × (4275/4500 - 1) = -5000
    led.close(
        position_id=pos.id,
        exit_index_level=Decimal("4275"),
        at=_T0 + timedelta(days=7),
    ).unwrap()
    assert led.realized_pnl_gross() == Decimal("-5000")
    # Loss passes through pre-tax (REQ_C_TAX_001 family).
    assert led.realized_pnl_after_tax() == Decimal("-5000")


def test_realized_after_tax_mixed_gain_and_loss() -> None:
    led = OverlayLedger()
    g = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    led.close(
        position_id=g.id,
        exit_index_level=Decimal("4725"),  # gain = 5000
        at=_T0 + timedelta(days=7),
    ).unwrap()
    loser = led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0 + timedelta(days=8),
    )
    led.close(
        position_id=loser.id,
        exit_index_level=Decimal("4275"),  # loss = -5000
        at=_T0 + timedelta(days=14),
    ).unwrap()
    # Gain ⇒ 3500 after tax; loss ⇒ -5000 pass-through ⇒ total -1500.
    assert led.realized_pnl_after_tax() == Decimal("-1500.00")


def test_realized_gross_excludes_open_positions() -> None:
    led = OverlayLedger()
    led.open(
        benchmark="EUROSTOXX50",
        notional=Decimal("100000"),
        entry_index_level=Decimal("4500"),
        at=_T0,
    )
    assert led.realized_pnl_gross() == Decimal("0")
    assert led.realized_pnl_after_tax() == Decimal("0")
