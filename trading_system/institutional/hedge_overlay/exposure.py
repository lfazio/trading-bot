"""Pure ``compute_portfolio_beta`` — REQ_F_HOV_002 / REQ_NF_HOV_001.

Two consecutive calls with identical ``(portfolio_returns,
benchmark_returns, window)`` SHALL return the same ``Result`` row
(determinism). The function reads no global state — no clock, no
RNG, no I/O.
"""

from __future__ import annotations

from decimal import Decimal

from trading_system.institutional.hedge_overlay.errors import OverlayError
from trading_system.result import Err, Ok, Result


def compute_portfolio_beta(
    portfolio_returns: tuple[Decimal, ...],
    *,
    benchmark_returns: tuple[Decimal, ...],
    window: int = 60,
) -> Result[Decimal, OverlayError]:
    """Rolling beta over the last ``window`` paired returns.

    Returns:
      - ``Ok(beta)`` — ``cov(p, b) / var(b)`` over the trailing window.
      - ``Err("hov:insufficient_history:<observed>/<required>")`` when
        either series has fewer than ``window`` observations.
      - ``Err("hov:degenerate_benchmark")`` when ``var(b) == 0``.

    Raises ``ValueError("hov:bad_window:<n>")`` when ``window < 2`` —
    the variance computation needs at least two observations to be
    meaningful (REQ_SDD_HOV_003).
    """
    if window < 2:
        raise ValueError(f"hov:bad_window:{window}")

    observed = min(len(portfolio_returns), len(benchmark_returns))
    if observed < window:
        return Err(
            OverlayError(
                f"hov:insufficient_history:{observed}/{window}",
                "compute_portfolio_beta",
            )
        )

    p = portfolio_returns[-window:]
    b = benchmark_returns[-window:]
    window_dec = Decimal(window)
    mean_b = sum(b, Decimal("0")) / window_dec
    var_b = sum(((bi - mean_b) ** 2 for bi in b), Decimal("0")) / window_dec
    if var_b == 0:
        return Err(OverlayError("hov:degenerate_benchmark"))
    mean_p = sum(p, Decimal("0")) / window_dec
    cov_pb = sum(
        ((pi - mean_p) * (bi - mean_b) for pi, bi in zip(p, b, strict=False)),
        Decimal("0"),
    ) / window_dec
    return Ok(cov_pb / var_b)
