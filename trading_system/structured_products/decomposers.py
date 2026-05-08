"""Per-payoff decomposers (REQ_SDD_ALG_012).

Each function in ``PAYOFF_DECOMPOSERS`` takes a ``StructuredProduct``
and returns a ``Decomposition`` if the payoff can be decomposed, or
``None`` if not (REQ_F_STP_002 + REQ_SDS_MOD_008: non-decomposable
products are rejected before any allocation logic).

The decomposers are pragmatic v1 implementations driven by the
product's barriers + payoff type. Operators tune the constants in
``config.py`` (deferred to a follow-up); callers can also pass
custom decomposers via ``AdmissionConfig.decomposers``.

Decomposition semantics:

- ``AUTOCALL`` — conditional equity + short-vol overlay.
  equity_equiv = 1.0 (full equity exposure between barriers);
  hidden_leverage = 0.5 (vol-selling tail);
  worst_case_loss bounded by the lowest barrier;
  break_even_prob = 0.7 (autocalls typically hit early when the
  underlying is flat-to-up).
- ``BARRIER`` — synthetic stock with a knockout floor.
  equity_equiv = 1.0; hidden_leverage = 0.0; worst_case_loss tied
  to the barrier distance; break_even_prob = 0.6.
- ``CAPITAL_PROT`` — bond + call. equity_equiv = 0.5; no hidden
  leverage; worst_case_loss = 0.05 (interest-rate / counterparty
  drift); break_even_prob = 0.5.
- ``LEV_CERT`` — leveraged certificate. equity_equiv = 1.0;
  hidden_leverage = (leverage - 1) drawn from ``barriers[0]`` if
  set; worst_case_loss = 0.4; break_even_prob = 0.4.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from trading_system.models.instrument import StructuredProduct
from trading_system.structured_products.decomposition import Decomposition

Decomposer = Callable[[StructuredProduct], Decomposition | None]


def _decompose_autocall(p: StructuredProduct) -> Decomposition | None:
    if not p.barriers:
        return None
    # Worst-case loss = how far below the lowest barrier the
    # product can fall, capped at full notional.
    worst = min(_safe_decimal_min(p.barriers, default=Decimal("0.6")), Decimal(1))
    return Decomposition(
        equity_equiv=Decimal("1.0"),
        hidden_leverage=Decimal("0.5"),
        worst_case_loss=worst,
        break_even_prob=Decimal("0.7"),
    )


def _decompose_barrier(p: StructuredProduct) -> Decomposition | None:
    if not p.barriers:
        return None
    barrier = _safe_decimal_min(p.barriers, default=Decimal("0.7"))
    # Loss kicks in at the barrier; tighter barriers mean less
    # downside before the knockout fires.
    worst = max(Decimal(0), Decimal(1) - barrier)
    return Decomposition(
        equity_equiv=Decimal("1.0"),
        hidden_leverage=Decimal("0.0"),
        worst_case_loss=min(worst, Decimal(1)),
        break_even_prob=Decimal("0.6"),
    )


def _decompose_capital_prot(p: StructuredProduct) -> Decomposition | None:
    # Capital-protected note doesn't strictly need barriers — the
    # protection is the floor. We accept any product flagged as
    # CAPITAL_PROT.
    _ = p  # silence unused-arg lint; reserved for future tuning
    return Decomposition(
        equity_equiv=Decimal("0.5"),
        hidden_leverage=Decimal("0.0"),
        worst_case_loss=Decimal("0.05"),
        break_even_prob=Decimal("0.5"),
    )


def _decompose_lev_cert(p: StructuredProduct) -> Decomposition | None:
    # First barrier is the stated leverage when present; default 2x.
    leverage = _safe_first(p.barriers, default=Decimal(2))
    if leverage < Decimal(1):
        return None
    return Decomposition(
        equity_equiv=Decimal("1.0"),
        hidden_leverage=leverage - Decimal(1),
        worst_case_loss=Decimal("0.4"),
        break_even_prob=Decimal("0.4"),
    )


PAYOFF_DECOMPOSERS: dict[str, Decomposer] = {
    "AUTOCALL": _decompose_autocall,
    "BARRIER": _decompose_barrier,
    "CAPITAL_PROT": _decompose_capital_prot,
    "LEV_CERT": _decompose_lev_cert,
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _safe_decimal_min(values: tuple[Decimal, ...], default: Decimal) -> Decimal:
    if not values:
        return default
    return min(values)


def _safe_first(values: tuple[Decimal, ...], default: Decimal) -> Decimal:
    if not values:
        return default
    return values[0]
