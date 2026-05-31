"""Tests for ``trading_system.strategy_lab.metrics``.

REQ refs: REQ_F_MTO_003, REQ_F_MTO_006, REQ_F_MTO_008.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.strategy_lab.metrics import (
    StrategyMetrics,
    format_signal_reason,
)


def _metrics(**overrides) -> StrategyMetrics:
    base = dict(
        net_after_tax_return=Decimal("0.10"),
        sharpe=Decimal("1.5"),
        stability=Decimal("0.7"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.1"),
        turnover=Decimal("12"),
        regime_stability=Decimal("0.6"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal("0.15"),
        return_=Decimal("0.10"),
    )
    base.update(overrides)
    return StrategyMetrics(**base)


def test_construction_default_values_pass() -> None:
    m = _metrics()
    assert m.sharpe == Decimal("1.5")


@pytest.mark.parametrize(
    "field",
    ["stability", "dd_penalty", "max_drawdown", "regime_stability", "parameter_sensitivity"],
)
def test_unit_interval_fields_rejected_outside_zero_one(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _metrics(**{field: Decimal("1.5")})


def test_negative_turnover_rejected() -> None:
    with pytest.raises(ValueError, match="turnover"):
        _metrics(turnover=Decimal("-1"))


def test_negative_leverage_rejected() -> None:
    with pytest.raises(ValueError, match="leverage"):
        _metrics(leverage=Decimal("-0.5"))


def test_negative_risk_rejected() -> None:
    with pytest.raises(ValueError, match="risk"):
        _metrics(risk=Decimal("-0.01"))


def test_indicator_signal_fields_default_to_none() -> None:
    """CR-028 follow-up — every `*_signal` field defaults to None so
    pre-CR-028 callers stay unaffected."""
    m = _metrics()
    assert m.sma_200_signal is None
    assert m.rsi_signal is None
    assert m.atr_signal is None
    assert m.obv_signal is None
    assert m.adx_signal is None
    assert m.vix_signal is None


def test_indicator_signal_fields_carry_decimal_when_set() -> None:
    """CR-028 follow-up — strategies that consume the indicator
    library populate the matching `*_signal` so the trade rationale
    (CR-015) carries the reading."""
    m = _metrics(
        sma_200_signal=Decimal("105.5"),
        rsi_signal=Decimal("65.0"),
        atr_signal=Decimal("1.85"),
        obv_signal=Decimal("12500"),
        adx_signal=Decimal("32.0"),
        vix_signal=Decimal("14.5"),
    )
    assert m.sma_200_signal == Decimal("105.5")
    assert m.rsi_signal == Decimal("65.0")
    assert m.atr_signal == Decimal("1.85")
    assert m.obv_signal == Decimal("12500")
    assert m.adx_signal == Decimal("32.0")
    assert m.vix_signal == Decimal("14.5")


# ---------------------------------------------------------------------------
# CR-028 + CR-015 follow-up — signal_reason helper
# ---------------------------------------------------------------------------


def test_format_signal_reason_empty_when_all_none() -> None:
    """No indicators consumed ⇒ empty string. Strategies that
    never opted in keep emitting empty `signal_reason` rows
    (back-compat)."""
    assert format_signal_reason() == ""


def test_format_signal_reason_orders_by_indicator_name() -> None:
    """Sorted indicator names regardless of kwarg order — the
    audit-trail bytes are stable across callers."""
    out = format_signal_reason(
        rsi=Decimal("68.2"),
        sma_200=Decimal("145.23"),
        atr=Decimal("2.51"),
    )
    # Alphabetical: adx, atr, obv, rsi, sma_200, vix
    # Present here: atr, rsi, sma_200
    assert out == "atr=2.51;rsi=68.2;sma_200=145.23"


def test_format_signal_reason_skips_none_values() -> None:
    """None entries are omitted from the output."""
    out = format_signal_reason(
        rsi=Decimal("68.2"),
        atr=None,
        obv=Decimal("12500"),
    )
    assert out == "obv=12500;rsi=68.2"


def test_format_signal_reason_decimal_canonical_serialisation() -> None:
    """REQ_NF_REP_001 family — Decimal values render via `str(...)`
    so the bytes are canonical-decimal stable (no float
    intermediate, no truncation)."""
    out = format_signal_reason(
        atr=Decimal("2.510000"),  # trailing zeros preserved by Decimal.__str__
        rsi=Decimal("0.0001"),
    )
    assert out == "atr=2.510000;rsi=0.0001"


def test_format_signal_reason_is_deterministic() -> None:
    """Two calls with identical kwargs SHALL produce byte-identical
    strings — precondition for the persistence layer's JSON
    round-trip + the backtest engine's replay invariant."""
    kwargs = {
        "sma_200": Decimal("145.23"),
        "rsi": Decimal("68.2"),
        "atr": Decimal("2.51"),
        "obv": Decimal("12500"),
        "adx": Decimal("32.0"),
        "vix": Decimal("14.5"),
    }
    assert format_signal_reason(**kwargs) == format_signal_reason(**kwargs)


def test_to_signal_reason_delegates_to_format_helper() -> None:
    """The instance method SHALL produce the same output as the
    standalone helper when given identical readings."""
    m = _metrics(
        sma_200_signal=Decimal("145.23"),
        rsi_signal=Decimal("68.2"),
        atr_signal=Decimal("2.51"),
    )
    assert m.to_signal_reason() == format_signal_reason(
        sma_200=Decimal("145.23"),
        rsi=Decimal("68.2"),
        atr=Decimal("2.51"),
    )


def test_to_signal_reason_empty_when_no_signals_consumed() -> None:
    """Default-constructed metrics (every signal None) renders
    as an empty signal_reason — strategies opting out of
    indicator readings preserve the legacy empty-string
    behaviour."""
    m = _metrics()
    assert m.to_signal_reason() == ""


def test_to_signal_reason_full_set() -> None:
    """All six indicators populated ⇒ all six in the output,
    alphabetically sorted."""
    m = _metrics(
        sma_200_signal=Decimal("105.5"),
        rsi_signal=Decimal("65.0"),
        atr_signal=Decimal("1.85"),
        obv_signal=Decimal("12500"),
        adx_signal=Decimal("32.0"),
        vix_signal=Decimal("14.5"),
    )
    assert (
        m.to_signal_reason()
        == "adx=32.0;atr=1.85;obv=12500;rsi=65.0;sma_200=105.5;vix=14.5"
    )
