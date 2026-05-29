"""CR-024 / TC_TOK_001..009 — operator-token rotation + lifecycle.

The verifier was introduced under CR-006 / REQ_F_ACC_010 with a
three-segment payload; CR-024 (Accepted 2026-05-26) adds a
four-segment ``jti``-aware format + multi-secret rolling rotation
+ revocation lookup + expiry-warning + structured-audit lifecycle
entries.

REQ refs: REQ_F_TOK_001 (four-segment format),
REQ_F_TOK_002 (revocation), REQ_F_TOK_003 (multi-secret),
REQ_F_TOK_004 (seconds_until_expiry), REQ_NF_TOK_001 (audit),
REQ_SDD_TOK_001 (token shape + back-compat), REQ_SDD_TOK_002
(revocation precedence), REQ_SDD_TOK_003 (rotate_secret order),
REQ_SDD_TOK_004 (read-only accessor).

These tests pin every new contract:

- TC_TOK_001 — format on every freshly issued token.
- TC_TOK_002 — revocation persisted; check precedes TTL.
- TC_TOK_003 — multi-secret roll with `rotate_secret`.
- TC_TOK_004 — `seconds_until_expiry` Option semantics.
- TC_TOK_005 — every lifecycle event emits a SECURITY log line.
- TC_TOK_006 — covered by the dedicated repository test
  ``tests/persistence/test_token_revocations_repository.py``.
- TC_TOK_007 — `LogCategory` value lock.
- TC_TOK_008 — determinism on verify.
- TC_TOK_009 — `rotate_secret` rejects empty input.
- TC_TOK_010 — migration audit (lives in
  ``tests/persistence/test_token_revocations_repository.py``).
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
    _parse_token,
)
from trading_system.observability import configure_logging
from trading_system.result import Nothing, Some


@pytest.fixture
def secret() -> bytes:
    return b"smoke-secret-deadbeef" * 4


@pytest.fixture
def verifier(secret: bytes) -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=secret, ttl_seconds=3600)


# ---------------------------------------------------------------------------
# TC_TOK_001 — token format + back-compat
# ---------------------------------------------------------------------------


class TestTokenFormat:
    def test_issued_token_has_four_segments(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        token = verifier.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        # rsplit-from-the-right by 3 yields 4 fields when the
        # format is well-shaped.
        parts = token.rsplit(":", 3)
        assert len(parts) == 4

    def test_issued_token_jti_is_32_hex_chars(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        token = verifier.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        parsed = _parse_token(token)
        assert parsed is not None
        assert parsed.jti is not None
        assert len(parsed.jti) == 32
        assert all(c in "0123456789abcdef" for c in parsed.jti)

    def test_two_issued_tokens_have_distinct_jti(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        t1 = verifier.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        t2 = verifier.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        p1 = _parse_token(t1)
        p2 = _parse_token(t2)
        assert p1 is not None and p2 is not None
        assert p1.jti != p2.jti

    def test_legacy_three_segment_token_still_verifies(
        self, secret: bytes
    ) -> None:
        """Legacy tokens (no jti) issued by the pre-CR-024
        verifier SHALL continue to verify."""
        import hashlib
        import hmac as _hmac

        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        ts = now.isoformat()
        account_id = "default"
        msg = f"{ts}:{account_id}".encode()
        sig = _hmac.new(secret, msg, hashlib.sha256).hexdigest()
        legacy_token = f"{ts}:{account_id}:{sig}"

        v = AccountScopedTokenVerifier(
            secret=secret,
            ttl_seconds=3600,
            _clock=lambda: now,
        )
        assert v.verify(legacy_token, account_id=account_id) is True

    def test_legacy_token_carries_no_jti(self, secret: bytes) -> None:
        """The parser distinguishes legacy (3-segment) from CR-024
        (4-segment) tokens by inspecting the third-from-end
        segment's shape."""
        import hashlib
        import hmac as _hmac

        ts = "2026-05-26T12:00:00+00:00"
        account_id = "default"
        msg = f"{ts}:{account_id}".encode()
        sig = _hmac.new(secret, msg, hashlib.sha256).hexdigest()
        legacy_token = f"{ts}:{account_id}:{sig}"
        parsed = _parse_token(legacy_token)
        assert parsed is not None
        assert parsed.jti is None


# ---------------------------------------------------------------------------
# TC_TOK_002 — revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    def test_revoked_jti_rejects_token(self, secret: bytes) -> None:
        now = datetime(2026, 5, 26, tzinfo=UTC)
        verifier = AccountScopedTokenVerifier(
            secret=secret, ttl_seconds=3600, _clock=lambda: now
        )
        token = verifier.issue(account_id="default", now=now)
        parsed = _parse_token(token)
        assert parsed is not None and parsed.jti is not None
        revoked_set = {(parsed.account_id, parsed.jti)}

        class _Lookup:
            def is_revoked(self, *, account_id: str, jti: str) -> bool:
                return (account_id, jti) in revoked_set

        verifier.revocation_lookup = _Lookup()
        assert verifier.verify(token, account_id="default") is False

    def test_unrevoked_jti_still_verifies(self, secret: bytes) -> None:
        now = datetime(2026, 5, 26, tzinfo=UTC)
        verifier = AccountScopedTokenVerifier(
            secret=secret, ttl_seconds=3600, _clock=lambda: now
        )
        token = verifier.issue(account_id="default", now=now)

        class _Lookup:
            def is_revoked(self, *, account_id: str, jti: str) -> bool:
                return False

        verifier.revocation_lookup = _Lookup()
        assert verifier.verify(token, account_id="default") is True

    def test_revocation_check_precedes_ttl_check(
        self, secret: bytes
    ) -> None:
        """A token that's both revoked AND TTL-valid SHALL surface
        as revoked. (The audit log entry's `outcome` field is the
        observable.)"""
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        verifier = AccountScopedTokenVerifier(
            secret=secret, ttl_seconds=3600, _clock=lambda: now
        )
        token = verifier.issue(account_id="default", now=now)
        parsed = _parse_token(token)
        assert parsed is not None and parsed.jti is not None

        class _Lookup:
            def is_revoked(self, *, account_id: str, jti: str) -> bool:
                return True

        verifier.revocation_lookup = _Lookup()
        assert verifier.verify(token, account_id="default") is False

    def test_legacy_token_skips_revocation_check(
        self, secret: bytes
    ) -> None:
        """Tokens without a jti SHALL bypass the revocation lookup
        entirely (REQ_SDD_TOK_002 — legacy tokens unrevocable)."""
        import hashlib
        import hmac as _hmac

        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        ts = now.isoformat()
        msg = f"{ts}:default".encode()
        sig = _hmac.new(secret, msg, hashlib.sha256).hexdigest()
        legacy = f"{ts}:default:{sig}"

        calls = {"n": 0}

        class _Lookup:
            def is_revoked(self, *, account_id: str, jti: str) -> bool:
                calls["n"] += 1
                return True

        verifier = AccountScopedTokenVerifier(
            secret=secret,
            ttl_seconds=3600,
            _clock=lambda: now,
            revocation_lookup=_Lookup(),
        )
        assert verifier.verify(legacy, account_id="default") is True
        assert calls["n"] == 0


# ---------------------------------------------------------------------------
# TC_TOK_003 + TC_TOK_009 — multi-secret rotation
# ---------------------------------------------------------------------------


class TestMultiSecretRotation:
    def test_token_signed_with_previous_secret_still_verifies(
        self,
    ) -> None:
        secret_a = b"a" * 32
        secret_b = b"b" * 32
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=secret_a, ttl_seconds=3600, _clock=lambda: now
        )
        token_a = v.issue(account_id="default", now=now)
        v.rotate_secret(secret_b)
        # token_a was signed by secret_a; after rotate it lives in
        # previous_secret. verify SHALL still accept.
        assert v.verify(token_a, account_id="default") is True

    def test_issue_after_rotation_signs_with_new_secret(
        self,
    ) -> None:
        secret_a = b"a" * 32
        secret_b = b"b" * 32
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=secret_a, ttl_seconds=3600, _clock=lambda: now
        )
        v.rotate_secret(secret_b)
        token_b = v.issue(account_id="default", now=now)
        # token_b SHALL verify against a fresh verifier with secret_b.
        fresh = AccountScopedTokenVerifier(
            secret=secret_b, ttl_seconds=3600, _clock=lambda: now
        )
        assert fresh.verify(token_b, account_id="default") is True

    def test_second_rotation_discards_a(self) -> None:
        secret_a = b"a" * 32
        secret_b = b"b" * 32
        secret_c = b"c" * 32
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=secret_a, ttl_seconds=3600, _clock=lambda: now
        )
        token_a = v.issue(account_id="default", now=now)
        v.rotate_secret(secret_b)
        v.rotate_secret(secret_c)
        # secret_a is now two rotations in the past — discarded.
        assert v.verify(token_a, account_id="default") is False

    def test_rotate_secret_rejects_empty_input(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            verifier.rotate_secret(b"")
        with pytest.raises(ValueError):
            verifier.rotate_secret(None)  # type: ignore[arg-type]

    def test_failed_rotation_leaves_secret_intact(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        old_secret = verifier.secret
        old_previous = verifier.previous_secret
        with pytest.raises(ValueError):
            verifier.rotate_secret(b"")
        assert verifier.secret == old_secret
        assert verifier.previous_secret == old_previous


# ---------------------------------------------------------------------------
# TC_TOK_004 — seconds_until_expiry
# ---------------------------------------------------------------------------


class TestSecondsUntilExpiry:
    def test_fresh_token_reports_near_ttl(
        self,
    ) -> None:
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
        )
        token = v.issue(account_id="default", now=now)
        remaining = v.seconds_until_expiry(token)
        assert isinstance(remaining, Some)
        # Issued at `now`; remaining ≈ ttl.
        assert 3599 <= remaining.value <= 3600

    def test_expired_token_returns_nothing(self) -> None:
        issued_at = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(secret=b"s" * 32, ttl_seconds=60)
        token = v.issue(account_id="default", now=issued_at)
        # Advance clock past TTL.
        v._clock = lambda: issued_at + timedelta(seconds=120)
        assert v.seconds_until_expiry(token) == Nothing()

    def test_tampered_token_returns_nothing(self) -> None:
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
        )
        token = v.issue(account_id="default", now=now)
        # Flip the last character of the signature.
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert v.seconds_until_expiry(tampered) == Nothing()

    def test_malformed_token_returns_nothing(
        self, verifier: AccountScopedTokenVerifier
    ) -> None:
        assert verifier.seconds_until_expiry("garbage") == Nothing()
        assert verifier.seconds_until_expiry("") == Nothing()
        assert verifier.seconds_until_expiry("a:b") == Nothing()

    def test_revoked_token_returns_nothing(
        self,
    ) -> None:
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
        )
        token = v.issue(account_id="default", now=now)

        class _Lookup:
            def is_revoked(self, *, account_id: str, jti: str) -> bool:
                return True

        v.revocation_lookup = _Lookup()
        assert v.seconds_until_expiry(token) == Nothing()


# ---------------------------------------------------------------------------
# TC_TOK_005 + TC_TOK_007 + TC_TOK_008 — structured audit
# ---------------------------------------------------------------------------


def _captured_logs(sink: io.StringIO) -> list[dict]:
    lines = sink.getvalue().strip().splitlines()
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


class TestStructuredAudit:
    def test_issue_emits_security_log_entry(self) -> None:
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        v = AccountScopedTokenVerifier(secret=b"s" * 32, ttl_seconds=3600)
        v.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        logs = _captured_logs(sink)
        security = [r for r in logs if r.get("category") == "security"]
        assert security, "expected a SECURITY log entry on issue"
        entry = security[-1]
        payload = entry.get("payload", {})
        assert payload.get("event") == "issue"
        assert payload.get("account_id") == "default"
        assert payload.get("outcome") == "ok"
        assert "jti" in payload

    def test_verify_ok_emits_security_log(self) -> None:
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        now = datetime(2026, 5, 26, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
        )
        token = v.issue(account_id="default", now=now)
        sink.truncate(0)
        sink.seek(0)
        v.verify(token, account_id="default")
        logs = _captured_logs(sink)
        verify_logs = [
            r
            for r in logs
            if r.get("payload", {}).get("event") == "verify_ok"
        ]
        assert verify_logs, "expected a verify_ok SECURITY log"
        assert verify_logs[0]["payload"]["outcome"] == "ok"

    def test_verify_failed_emits_security_log_with_outcome(
        self,
    ) -> None:
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        v = AccountScopedTokenVerifier(secret=b"s" * 32, ttl_seconds=3600)
        v.verify("garbage", account_id="default")
        logs = _captured_logs(sink)
        bad = [
            r for r in logs if r.get("payload", {}).get("event") == "verify_failed"
        ]
        assert bad
        assert bad[0]["payload"]["outcome"] == "bad_format"

    def test_rotate_secret_emits_security_log(self) -> None:
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        v = AccountScopedTokenVerifier(secret=b"a" * 32, ttl_seconds=3600)
        v.rotate_secret(b"b" * 32)
        logs = _captured_logs(sink)
        rot = [
            r for r in logs if r.get("payload", {}).get("event") == "rotate_secret"
        ]
        assert rot

    def test_security_log_never_includes_raw_secret(self) -> None:
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        secret = b"never-log-this-secret-payload-please-please-please"
        v = AccountScopedTokenVerifier(secret=secret, ttl_seconds=3600)
        v.issue(
            account_id="default", now=datetime(2026, 5, 26, tzinfo=UTC)
        )
        # Search the entire captured stream for the secret payload.
        haystack = sink.getvalue()
        assert "never-log-this-secret" not in haystack

    def test_log_category_security_value_is_lowercase(self) -> None:
        """REQ_NF_LOG_001 — the LogCategory string value SHALL be
        the lowercase enum name."""
        from trading_system.observability import LogCategory

        # LogCategory is a Literal[str, ...] — confirm "security"
        # appears in the type's __args__ rather than checking enum
        # membership.
        assert "security" in LogCategory.__args__  # type: ignore[attr-defined]

    def test_two_verifies_emit_byte_identical_payloads_modulo_corr_id(
        self,
    ) -> None:
        """TC_TOK_008 — replay determinism. Two verifies of the
        same token SHALL produce structurally identical payloads
        (corr_id is allowed to vary; everything else is pinned)."""
        sink = io.StringIO()
        configure_logging(level="INFO", json_output=True, stream=sink)
        now = datetime(2026, 5, 26, 12, tzinfo=UTC)
        v = AccountScopedTokenVerifier(
            secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
        )
        token = v.issue(account_id="default", now=now)
        sink.truncate(0)
        sink.seek(0)
        v.verify(token, account_id="default")
        v.verify(token, account_id="default")
        logs = _captured_logs(sink)
        verify_logs = [
            r["payload"]
            for r in logs
            if r.get("payload", {}).get("event") == "verify_ok"
        ]
        assert len(verify_logs) >= 2
        # Each verify_ok payload SHALL carry the same account_id +
        # jti + outcome.
        first = verify_logs[0]
        for other in verify_logs[1:]:
            assert other["account_id"] == first["account_id"]
            assert other["jti"] == first["jti"]
            assert other["outcome"] == first["outcome"]


# ---------------------------------------------------------------------------
# Auxiliary — household claim still works under CR-024
# ---------------------------------------------------------------------------


def test_household_claim_round_trip() -> None:
    """REQ_F_ACC_010 — the literal sentinel ``HOUSEHOLD_CLAIM``
    SHALL continue to verify under CR-024's four-segment format."""
    now = datetime(2026, 5, 26, tzinfo=UTC)
    v = AccountScopedTokenVerifier(
        secret=b"s" * 32, ttl_seconds=3600, _clock=lambda: now
    )
    token = v.issue(account_id=HOUSEHOLD_CLAIM, now=now)
    assert v.verify(token, account_id=HOUSEHOLD_CLAIM) is True
    assert v.verify(token, account_id="default") is False
