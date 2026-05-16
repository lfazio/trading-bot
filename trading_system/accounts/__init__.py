"""Multi-account orchestration — CR-006 Phase-6 foundation slice.

The runtime owns one or more :class:`Account` aggregates through an
:class:`AccountRegistry`; each account carries its own broker handle,
portfolio, capital-flow ledger, phase engine, tax model, and a
``RiskConfig`` overlay (the overlay can only *tighten* the system-
wide config per REQ_F_ACC_006). The :class:`PortfolioGroup` aggregates
read-only views across accounts; ``cross_account_risk.gate`` enforces
the single-asset concentration cap across the household; the
:class:`HouseholdDrawdownTrigger` produces ``KillSwitchTrigger`` rows
when household drawdown breaches the configured floor.

Backwards compatibility (REQ_F_ACC_003 / REQ_NF_ACC_001):
a deployment without ``config/accounts.yaml`` SHALL keep working —
:func:`build_single_account_default` synthesises an ``Account(id=
"default")`` from the legacy ``system.yaml`` / ``phases.yaml`` /
``risk.yaml`` files.

Runtime wiring (``main.py`` traversing the registry, ``Backtest``
taking an ``Account`` reference instead of bare components) is a
follow-up slice — the foundation here is purely additive.

REQ refs: REQ_F_ACC_001..010, REQ_NF_ACC_001, REQ_SDS_ACC_001..004,
REQ_SDD_ACC_001..008.
"""

from trading_system.accounts.account import Account
from trading_system.accounts.cross_account_risk import (
    cross_account_concentration_gate,
)
from trading_system.accounts.group import PortfolioGroup
from trading_system.accounts.household_drawdown_trigger import (
    HouseholdDrawdownTrigger,
)
from trading_system.accounts.registry import AccountRegistry
from trading_system.accounts.tax_model import (
    FranceCTOTaxModel,
    TaxModel,
)
from trading_system.accounts.token_verifier import (
    AccountScopedTokenVerifier,
)

__all__ = [
    "Account",
    "AccountRegistry",
    "AccountScopedTokenVerifier",
    "FranceCTOTaxModel",
    "HouseholdDrawdownTrigger",
    "PortfolioGroup",
    "TaxModel",
    "cross_account_concentration_gate",
]
