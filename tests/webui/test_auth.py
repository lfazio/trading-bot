"""Tests for ``WebAuth`` (REQ_F_WEB_005 / REQ_SDD_ACC_007)."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Ok
from trading_system.webui.auth import WebAuth


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _auth(secret: bytes = b"shh") -> tuple[WebAuth, AccountScopedTokenVerifier]:
    verifier = AccountScopedTokenVerifier(
        secret=secret, ttl_seconds=300, _clock=lambda: _NOW
    )
    return WebAuth(verifier=verifier), verifier


# ---------------------------------------------------------------------------
# require_account
# ---------------------------------------------------------------------------


def test_require_account_accepts_valid_bearer_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    res = auth.require_account(
        {"Authorization": f"Bearer {token}"},
        AccountId("alpha"),
    )
    assert isinstance(res, Ok)


def test_require_account_accepts_legacy_x_operator_token_header() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    res = auth.require_account(
        {"X-Operator-Token": token},
        AccountId("alpha"),
    )
    assert isinstance(res, Ok)


def test_require_account_rejects_missing_header() -> None:
    auth, _v = _auth()
    match auth.require_account({}, AccountId("alpha")):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


def test_require_account_rejects_empty_bearer_header() -> None:
    auth, _v = _auth()
    match auth.require_account({"Authorization": "Bearer "}, AccountId("alpha")):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


def test_require_account_rejects_token_for_wrong_account() -> None:
    """REQ_SDD_ACC_007 — token signed for ``beta`` SHALL be rejected
    when verified against ``alpha``."""
    auth, verifier = _auth()
    token_for_beta = verifier.issue(account_id="beta", now=_NOW)
    match auth.require_account(
        {"Authorization": f"Bearer {token_for_beta}"},
        AccountId("alpha"),
    ):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


def test_require_account_lowercase_authorization_header() -> None:
    """Case-insensitive header lookup matches the HTTP RFC + stdlib
    http.server behaviour (which lowercases header names by
    default)."""
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    res = auth.require_account(
        {"authorization": f"Bearer {token}"},
        AccountId("alpha"),
    )
    assert isinstance(res, Ok)


# ---------------------------------------------------------------------------
# require_household
# ---------------------------------------------------------------------------


def test_require_household_accepts_household_claim() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    res = auth.require_household({"Authorization": f"Bearer {token}"})
    assert isinstance(res, Ok)


def test_require_household_rejects_per_account_token() -> None:
    """A token signed for an account ID SHALL NOT pass the household
    check (REQ_SDD_ACC_007)."""
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    match auth.require_household({"Authorization": f"Bearer {token}"}):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")


def test_require_household_rejects_missing_header() -> None:
    auth, _v = _auth()
    match auth.require_household({}):
        case Err(reason):
            assert reason == "registry:token_invalid"
        case _:
            raise AssertionError("expected Err")
