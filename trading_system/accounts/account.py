"""``Account`` aggregate — frozen identity over its lower-layer
components (REQ_F_ACC_001 / REQ_SDD_ACC_001).

The aggregate is *identity-only frozen* — the references it carries
point at separately-owned mutable cursors (Portfolio, CapitalFlow,
broker handle). Mutating the underlying cursors is fine; mutating
the ``Account`` reference set itself is not. Cross-account leakage
of state is a programmer-error invariant — the Phase-6 follow-up
adds runtime guards if needed.

REQ refs: REQ_F_ACC_001, REQ_F_ACC_004, REQ_F_ACC_005, REQ_F_ACC_006,
REQ_SDD_ACC_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_system.accounts.tax_model import TaxModel
from trading_system.models.identifiers import AccountId


# ``Any`` here is intentional: the Phase-6 foundation slice doesn't
# tie ``Account`` to the concrete broker / portfolio / capital_flow
# / phase_engine / risk-config types. The follow-up slice will lock
# the structural types once the broker-adapter Protocol and
# phase-engine per-account API are finalised. Keeping the field
# types open at this stage avoids forcing premature changes in the
# legacy callers (REQ_NF_ACC_001 backwards-compat).
_AnyComponent = Any


@dataclass(frozen=True, slots=True)
class Account:
    """The runtime's per-account container.

    ``id`` is the canonical key — every persisted row downstream
    (REQ_F_PER_009 / REQ_SDD_PER_008) uses the same ``AccountId``.

    Fields:
    - ``id`` — :class:`AccountId` newtype.
    - ``broker`` — the adapter handle (concrete or
      ``LocalBrokerAdapter``).
    - ``portfolio`` — per-account ``Portfolio`` reference.
    - ``capital_flow`` — per-account ``CapitalFlow`` reference.
    - ``phase_engine`` — per-account ``PhaseEngine`` instance
      (REQ_F_ACC_004 — each account resolves its own phase from its
      own capital pool).
    - ``tax_model`` — :class:`TaxModel` Protocol implementation
      (REQ_F_ACC_005 — defaults to ``FranceCTOTaxModel`` unless the
      operator binds something else in ``accounts.yaml``).
    - ``risk_overlay`` — per-account ``RiskConfig`` overlay
      (REQ_F_ACC_006 — unset fields inherit; the overlay can only
      *tighten* the phase-derived caps, never relax them; the
      tightening invariant is enforced by the registry on insert,
      not on ``Account`` construction, so this dataclass stays
      additive).
    - ``operator_token_account_id`` — the literal ``account_id``
      that operator tokens for this account SHALL carry (REQ_F_ACC_010 /
      REQ_SDD_ACC_007). For the single-account default, this equals
      ``str(id)`` (i.e. ``"default"``).
    """

    id: AccountId
    broker: _AnyComponent
    portfolio: _AnyComponent
    capital_flow: _AnyComponent
    phase_engine: _AnyComponent
    tax_model: TaxModel
    risk_overlay: _AnyComponent
    operator_token_account_id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Account.id must be non-empty")
        if not self.operator_token_account_id.strip():
            raise ValueError(
                "Account.operator_token_account_id must be non-empty"
            )
        if not isinstance(self.tax_model, TaxModel):
            raise TypeError(
                "Account.tax_model must satisfy the TaxModel Protocol, "
                f"got {type(self.tax_model).__name__}"
            )
