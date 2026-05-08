"""``admit()`` — gate-ordered structured-product admission.

Gates (reject on first failure; one categorised reason per gate):

1. ``regime_forbidden`` — REQ_F_STP_003 / 004: only BULL or
   SIDEWAYS regimes allow new SP positions.
2. ``not_decomposable`` — REQ_F_STP_002 / REQ_SDS_MOD_008: payoff
   types without a registered decomposer (or whose decomposer
   returns None) are rejected before any allocation math.
3. ``stack_with_turbo`` — REQ_F_STP_007: cannot open an SP on an
   underlying that already has a turbo position.
4. ``cap_breach`` — REQ_F_STP_001: total SP exposure capped at 10%
   of equity by default.
5. ``issuer_concentration`` — REQ_F_STP_006 / REQ_SDD_ALG_014:
   single issuer share of equity capped at 25% by default.
6. ``stress_failed`` — REQ_F_STP_005 / REQ_SDD_ALG_013: every
   stress scenario's loss must be bounded by the decomposition's
   worst-case loss.

REQ refs: REQ_F_STP_001..007, REQ_SDS_MOD_008, REQ_SDD_ALG_012,
REQ_SDD_ALG_013, REQ_SDD_ALG_014.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_system.models.instrument import StructuredProduct
from trading_system.models.phase import AllocationBucket, MarketRegime
from trading_system.portfolio.portfolio import Portfolio
from trading_system.result import Err, Ok, Result
from trading_system.structured_products.decomposers import (
    PAYOFF_DECOMPOSERS,
    Decomposer,
)
from trading_system.structured_products.decomposition import Decomposition
from trading_system.structured_products.stress import stress_pass

# REQ_F_STP_001 — total SP allocation capped at 10% of equity.
_DEFAULT_STRUCTURED_CAP = Decimal("0.10")
# REQ_F_STP_006 / REQ_SDD_ALG_014 — single-issuer cap (share of equity).
_DEFAULT_ISSUER_CAP = Decimal("0.25")
# REQ_F_STP_003 / 004 — admit only in BULL or SIDEWAYS.
_DEFAULT_ALLOWED_REGIMES: frozenset[MarketRegime] = frozenset(
    {MarketRegime.BULL, MarketRegime.SIDEWAYS}
)


@dataclass(frozen=True, slots=True)
class AdmissionConfig:
    """Operator-tunable knobs for the admission gate."""

    structured_cap: Decimal = _DEFAULT_STRUCTURED_CAP
    issuer_cap: Decimal = _DEFAULT_ISSUER_CAP
    allowed_regimes: frozenset[MarketRegime] = field(
        default_factory=lambda: _DEFAULT_ALLOWED_REGIMES
    )
    decomposers: dict[str, Decomposer] = field(default_factory=lambda: dict(PAYOFF_DECOMPOSERS))

    def __post_init__(self) -> None:
        if not (Decimal(0) <= self.structured_cap <= Decimal(1)):
            raise ValueError(
                f"AdmissionConfig.structured_cap must lie in [0, 1], got {self.structured_cap}"
            )
        if not (Decimal(0) <= self.issuer_cap <= Decimal(1)):
            raise ValueError(
                f"AdmissionConfig.issuer_cap must lie in [0, 1], got {self.issuer_cap}"
            )
        if not self.allowed_regimes:
            raise ValueError("AdmissionConfig.allowed_regimes must be non-empty")


def admit(  # noqa: PLR0911 — gate-ordered; one return per gate by design
    product: StructuredProduct,
    proposed_allocation_pct: Decimal,
    regime: MarketRegime,
    portfolio: Portfolio,
    cfg: AdmissionConfig | None = None,
) -> Result[Decomposition, str]:
    """Run every admission gate; return the decomposition on
    success, or a categorised ``Err`` on the first failure.

    ``proposed_allocation_pct`` is the share of equity the operator
    intends to allocate to this product (e.g., Decimal("0.03") for
    3% of equity). The caller computes it from the product's
    notional and the live portfolio equity; we keep the API
    explicit so the gate doesn't reach into pricing logic.
    """
    cfg = cfg or AdmissionConfig()
    if not (Decimal(0) <= proposed_allocation_pct <= Decimal(1)):
        return Err(f"data:bad_allocation_pct:{proposed_allocation_pct}")

    # Gate 1 — regime.
    if regime not in cfg.allowed_regimes:
        return Err(f"regime_forbidden:{regime.value}")

    # Gate 2 — decomposability (REQ_F_STP_002 / REQ_SDS_MOD_008).
    decomposer = cfg.decomposers.get(product.payoff)
    if decomposer is None:
        return Err(f"not_decomposable:no_decomposer:{product.payoff}")
    decomp = decomposer(product)
    if decomp is None:
        return Err(f"not_decomposable:{product.payoff}:{product.id}")

    # Gate 3 — turbo-stack ban (REQ_F_STP_007).
    if portfolio.has_turbo_on(product.underlying):
        return Err(f"stack_with_turbo:{product.underlying}")

    # Gate 4 — total SP allocation cap (REQ_F_STP_001).
    current_sp = portfolio.exposure_pct(AllocationBucket.STRUCTURED)
    if current_sp + proposed_allocation_pct > cfg.structured_cap:
        return Err(f"cap_breach:{current_sp + proposed_allocation_pct}>{cfg.structured_cap}")

    # Gate 5 — issuer concentration (REQ_F_STP_006 / REQ_SDD_ALG_014).
    cur_issuer = portfolio.issuer_concentration(product.issuer)
    if cur_issuer + proposed_allocation_pct > cfg.issuer_cap:
        return Err(
            f"issuer_concentration:{product.issuer}:"
            f"{cur_issuer + proposed_allocation_pct}>{cfg.issuer_cap}"
        )

    # Gate 6 — stress (REQ_F_STP_005 / REQ_SDD_ALG_013).
    if not stress_pass(decomp):
        return Err(f"stress_failed:{product.id}")

    return Ok(decomp)
