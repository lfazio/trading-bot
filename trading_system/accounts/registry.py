"""``AccountRegistry`` — owns the runtime's collection of accounts
and fans the per-tick decisioning out deterministically.

Iteration is sorted by ``AccountId`` (alphabetical) so a
multi-account backtest replays bit-identically under REQ_NF_DET_001 /
REQ_SDS_ACC_002. The registry is the single mutable element of the
package — ``add()`` is the only insertion point; once added, an
account's identity cannot change (the dataclass is frozen).

REQ refs: REQ_F_ACC_002, REQ_F_ACC_003, REQ_NF_ACC_001,
REQ_SDS_ACC_002, REQ_SDD_ACC_002.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from trading_system.accounts.account import Account
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@dataclass(slots=True)
class AccountRegistry:
    """Read/write surface for the runtime's account collection."""

    _accounts: dict[AccountId, Account] = field(default_factory=dict)

    def add(self, account: Account) -> Result[None, str]:
        """Insert ``account``; duplicate id surfaces as
        ``Err("accounts:duplicate_id:<id>")``."""
        if account.id in self._accounts:
            return Err(f"accounts:duplicate_id:{account.id}")
        self._accounts[account.id] = account
        return Ok(None)

    def get(self, account_id: AccountId) -> Option[Account]:
        """Read-only lookup."""
        existing = self._accounts.get(account_id)
        if existing is None:
            return Nothing()
        return Some(existing)

    def list_accounts(self) -> tuple[Account, ...]:
        """Return every registered account sorted by id (alphabetical)
        — guarantees deterministic iteration across runs
        (REQ_NF_DET_001 / REQ_SDS_ACC_002 / REQ_SDD_ACC_002)."""
        return tuple(self._accounts[a] for a in sorted(self._accounts))

    def size(self) -> int:
        return len(self._accounts)

    def is_empty(self) -> bool:
        return not self._accounts

    def is_single_account(self) -> bool:
        """Used by ``cross_account_risk.gate`` to short-circuit the
        gate as a no-op in single-account deployments (REQ_NF_ACC_001)."""
        return len(self._accounts) == 1

    def ids(self) -> Iterable[AccountId]:
        """Sorted iterator over account ids — useful for operator
        tooling and dashboards that need to render the household
        composition deterministically."""
        return iter(sorted(self._accounts))


def is_default_single_account(registry: AccountRegistry) -> bool:
    """``True`` iff the registry holds exactly one account whose id
    is :data:`DEFAULT_ACCOUNT_ID`. Useful for legacy code paths that
    branch on "is this a backwards-compat single-account deployment"
    (REQ_NF_ACC_001)."""
    if not registry.is_single_account():
        return False
    only = next(iter(registry.list_accounts()))
    return only.id == DEFAULT_ACCOUNT_ID
