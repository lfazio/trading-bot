"""Tests for ``trading_system.accounts.token_verifier``.

Covers TC_ACC_009 (operator-token account-id claim + household-scoped
read sentinel).

REQ refs: REQ_F_ACC_010, REQ_S_KS_009, REQ_SDD_ACC_007.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)


def _verifier(*, now: datetime, ttl: int = 300) -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(
        secret=b"test-secret",
        ttl_seconds=ttl,
        _clock=lambda: now,
    )


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Default _clock SHALL return a timezone-aware datetime so the
# subtraction in verify() doesn't trip when issued_at carries an
# offset (operators routinely call ``issue(now=datetime.now(UTC))``).
# Regression for the user-manual snippet that surfaced this bug.
# ---------------------------------------------------------------------------


def test_default_clock_is_timezone_aware() -> None:
    """The default ``_clock`` SHALL return a tz-aware datetime so a
    token issued with ``datetime.now(UTC)`` verifies successfully
    without the caller having to inject a clock."""
    v = AccountScopedTokenVerifier(secret=b"shh")
    token = v.issue(account_id="alpha", now=datetime.now(UTC))
    assert v.verify(token, account_id="alpha") is True


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_secret_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="secret"):
        AccountScopedTokenVerifier(secret=b"")


def test_ttl_must_be_positive() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        AccountScopedTokenVerifier(secret=b"x", ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds"):
        AccountScopedTokenVerifier(secret=b"x", ttl_seconds=-1)


def test_issue_requires_non_empty_account_id() -> None:
    v = _verifier(now=_NOW)
    with pytest.raises(ValueError, match="account_id"):
        v.issue(account_id="", now=_NOW)
    with pytest.raises(ValueError, match="account_id"):
        v.issue(account_id="   ", now=_NOW)


# ---------------------------------------------------------------------------
# TC_ACC_009 — account-id claim verification
# ---------------------------------------------------------------------------


def test_token_verifies_against_matching_account() -> None:
    v = _verifier(now=_NOW)
    token = v.issue(account_id="alpha", now=_NOW)
    assert v.verify(token, account_id="alpha") is True


def test_token_rejected_when_account_id_mismatches() -> None:
    """REQ_F_ACC_010 / REQ_SDD_ACC_007 — a token issued for 'alpha'
    SHALL be rejected when verified against 'beta', even though
    the secret + timestamp are valid."""
    v = _verifier(now=_NOW)
    token = v.issue(account_id="alpha", now=_NOW)
    assert v.verify(token, account_id="beta") is False


def test_household_claim_accepted_for_household_reads() -> None:
    """Read endpoints SHALL accept tokens whose claim is the literal
    sentinel HOUSEHOLD_CLAIM."""
    v = _verifier(now=_NOW)
    token = v.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    assert v.verify(token, account_id=HOUSEHOLD_CLAIM) is True
    # Mutation endpoint calling with a per-account claim rejects the
    # household-scoped token (the caller's responsibility — the
    # verifier just enforces claim equality).
    assert v.verify(token, account_id="alpha") is False


def test_expired_token_rejected() -> None:
    issued_at = datetime(2026, 5, 16, 11, 50, tzinfo=UTC)
    later = issued_at + timedelta(seconds=400)  # ttl=300 → expired
    v_issue = _verifier(now=issued_at, ttl=300)
    token = v_issue.issue(account_id="alpha", now=issued_at)
    v_verify = _verifier(now=later, ttl=300)
    assert v_verify.verify(token, account_id="alpha") is False


def test_token_at_exact_ttl_boundary_accepted() -> None:
    issued_at = datetime(2026, 5, 16, 11, 50, tzinfo=UTC)
    at_boundary = issued_at + timedelta(seconds=300)  # exactly at ttl
    v_issue = _verifier(now=issued_at, ttl=300)
    token = v_issue.issue(account_id="alpha", now=issued_at)
    v_verify = _verifier(now=at_boundary, ttl=300)
    assert v_verify.verify(token, account_id="alpha") is True


def test_malformed_token_rejected() -> None:
    v = _verifier(now=_NOW)
    # Missing parts.
    assert v.verify("only-one-part", account_id="alpha") is False
    assert v.verify("two:parts", account_id="alpha") is False
    # Too many parts.
    assert v.verify("a:b:c:d", account_id="alpha") is False
    # Empty middle / signature.
    assert v.verify("ts::sig", account_id="alpha") is False
    assert v.verify("ts:acct:", account_id="alpha") is False


def test_tampered_signature_rejected() -> None:
    v = _verifier(now=_NOW)
    token = v.issue(account_id="alpha", now=_NOW)
    # Replace the trailing signature with junk.
    parts = token.split(":")
    tampered = ":".join(parts[:-1] + ["deadbeef" * 8])
    assert v.verify(tampered, account_id="alpha") is False


def test_tampered_account_id_rejected() -> None:
    """Modifying the account_id portion of an issued token SHALL
    invalidate the signature."""
    v = _verifier(now=_NOW)
    token = v.issue(account_id="alpha", now=_NOW)
    parts = token.split(":")
    tampered = ":".join([parts[0], "beta", parts[2]])
    assert v.verify(tampered, account_id="beta") is False
    assert v.verify(tampered, account_id="alpha") is False
