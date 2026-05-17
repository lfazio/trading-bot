"""TC_HOV_003 + TC_HOV_004 — ``compute_portfolio_beta``.

REQ refs:
- REQ_F_HOV_002 — pure rolling beta.
- REQ_NF_HOV_001 — replay determinism (identical inputs ⇒ identical
  Result).
- REQ_SDD_HOV_003 — ``window < 2`` SHALL raise
  ``ValueError("hov:bad_window:<n>")`` at the call site.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_system.institutional.hedge_overlay import compute_portfolio_beta
from trading_system.result import Err, Ok


def _series(values: list[str]) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in values)


# ---------------------------------------------------------------------------
# TC_HOV_003 — happy path + determinism
# ---------------------------------------------------------------------------


def test_beta_is_one_when_portfolio_equals_benchmark() -> None:
    """Identical series ⇒ beta = 1.0."""
    p = _series(["0.01", "-0.02", "0.005", "-0.01", "0.03"] * 12)
    b = _series(["0.01", "-0.02", "0.005", "-0.01", "0.03"] * 12)
    res = compute_portfolio_beta(p, benchmark_returns=b, window=60)
    match res:
        case Ok(beta):
            assert abs(beta - Decimal("1")) < Decimal("1e-9")
        case Err(reason):
            raise AssertionError(reason)


def test_beta_is_two_when_portfolio_doubles_benchmark() -> None:
    """``portfolio = 2 × benchmark`` ⇒ beta = 2."""
    b_pattern = ["0.01", "-0.02", "0.005", "-0.01", "0.03"] * 12
    p_pattern = [str(Decimal(v) * Decimal("2")) for v in b_pattern]
    p = _series(p_pattern)
    b = _series(b_pattern)
    res = compute_portfolio_beta(p, benchmark_returns=b, window=60)
    beta = res.unwrap()
    assert abs(beta - Decimal("2")) < Decimal("1e-9")


def test_beta_replay_determinism() -> None:
    """REQ_NF_HOV_001 — two consecutive calls return equal results."""
    p = _series(["0.01", "0.02", "-0.005"] * 30)
    b = _series(["0.008", "0.015", "-0.003"] * 30)
    a = compute_portfolio_beta(p, benchmark_returns=b, window=60)
    z = compute_portfolio_beta(p, benchmark_returns=b, window=60)
    assert a == z


def test_window_default_is_sixty() -> None:
    p = _series(["0.01"] * 60)
    b = _series(["0.01"] * 60)
    # Default window=60 — 60 observations is the boundary.
    res = compute_portfolio_beta(p, benchmark_returns=b)
    assert isinstance(res, Err)  # degenerate-benchmark — zero variance


# ---------------------------------------------------------------------------
# TC_HOV_004 — categorised Errs
# ---------------------------------------------------------------------------


def test_insufficient_history_err() -> None:
    p = _series(["0.01"] * 59)
    b = _series(["0.01"] * 60)
    match compute_portfolio_beta(p, benchmark_returns=b, window=60):
        case Err(reason):
            assert reason.category == "hov:insufficient_history:59/60"
        case _:
            raise AssertionError("expected Err on insufficient history")


def test_degenerate_benchmark_err() -> None:
    """Zero-variance benchmark ⇒ ``hov:degenerate_benchmark``."""
    p = _series([f"{0.01 * (i - 30)}" for i in range(60)])
    b = _series(["0"] * 60)  # flat
    match compute_portfolio_beta(p, benchmark_returns=b, window=60):
        case Err(reason):
            assert reason.category == "hov:degenerate_benchmark"
        case _:
            raise AssertionError("expected Err on flat benchmark")


def test_bad_window_raises_value_error() -> None:
    """REQ_SDD_HOV_003 — ``window < 2`` is a panic (programmer error)."""
    p = _series(["0.01"] * 60)
    b = _series(["0.01"] * 60)
    with pytest.raises(ValueError, match="hov:bad_window:1"):
        compute_portfolio_beta(p, benchmark_returns=b, window=1)
