"""``WebAuth`` — HTTP auth wrapper.

Reads the ``Authorization: Bearer <token>`` header (or the legacy
``X-Operator-Token`` header for tooling) and verifies it through
``AccountScopedTokenVerifier`` (REQ_F_WEB_005 / REQ_SDD_ACC_007).
Two scopes:

- ``require_account(headers, account_id)`` — per-account mutations.
- ``require_household(headers)`` — read endpoints that span the
  household. The token's claim SHALL be the literal sentinel
  ``HOUSEHOLD_CLAIM`` (REQ_SDD_ACC_007).

The verifier's closed Err category is reused so call sites
pattern-match the same ``registry:token_invalid`` string as the
existing registry-promotion path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok, Result


_BEARER_PREFIX = "Bearer "


@dataclass(slots=True)
class WebAuth:
    """Authoriser for HTTP routes.

    A single instance is shared across the route registry; it's
    pure modulo the wrapped verifier so concurrent requests are
    safe.
    """

    verifier: AccountScopedTokenVerifier

    def require_account(
        self,
        headers: Mapping[str, str],
        account_id: AccountId,
    ) -> Result[None, str]:
        """Verify the token in ``headers`` against ``account_id``.

        Returns ``Err("registry:token_invalid")`` on signature
        mismatch, claim mismatch, missing header, or expired token.
        Same closed Err category as ``RegistryRepository.request_promotion``.
        """
        token = _extract_token(headers)
        if token is None:
            return Err("registry:token_invalid")
        if not self.verifier.verify(token, account_id=str(account_id)):
            return Err("registry:token_invalid")
        return Ok(None)

    def require_household(self, headers: Mapping[str, str]) -> Result[None, str]:
        """Verify the token in ``headers`` carries the household
        sentinel claim. Read endpoints SHALL use this; mutation
        endpoints SHALL use ``require_account``."""
        token = _extract_token(headers)
        if token is None:
            return Err("registry:token_invalid")
        if not self.verifier.verify(token, account_id=HOUSEHOLD_CLAIM):
            return Err("registry:token_invalid")
        return Ok(None)


def _extract_token(headers: Mapping[str, str]) -> str | None:
    """Read the token from the standard ``Authorization`` header
    (Bearer scheme) or the legacy ``X-Operator-Token`` header
    operator tooling uses.

    Header name lookup is case-insensitive — the underlying server
    SHALL hand the routes a CIMultiDict-style mapping; we tolerate
    a plain dict by trying the documented capitalisations.
    """
    for key in ("Authorization", "authorization"):
        raw = headers.get(key)
        if raw is None:
            continue
        if raw.startswith(_BEARER_PREFIX):
            return raw[len(_BEARER_PREFIX) :].strip() or None
        return raw.strip() or None
    for key in ("X-Operator-Token", "x-operator-token"):
        raw = headers.get(key)
        if raw is not None:
            return raw.strip() or None
    return None
