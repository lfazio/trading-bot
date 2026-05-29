"""``AccountScopedTokenVerifier`` — HMAC-SHA256 operator-token verifier.

The verifier was introduced under CR-006 / REQ_F_ACC_010 with a
three-segment payload `<iso_timestamp>:<account_id>:<hex_signature>`.
CR-024 (Accepted 2026-05-26) extends it with a token-id (`jti`),
multi-secret rolling rotation, revocation, expiry-warning, and
structured-audit lifecycle entries — without breaking the legacy
three-segment token format (existing tokens continue to verify).

Token format (CR-024):

    <iso_timestamp>:<account_id>:<jti>:<hex_signature>

where:
- ``iso_timestamp`` — ISO-8601 datetime with timezone (UTC offset).
- ``account_id`` — literal target account or ``HOUSEHOLD_CLAIM``
  for read-only household-scope tokens.
- ``jti`` — 32-char ``uuid4().hex`` token id (REQ_SDD_TOK_001) so
  revocation has a stable handle.
- ``hex_signature`` — HMAC-SHA256 over
  ``<iso_timestamp>:<account_id>:<jti>``.

Legacy three-segment tokens (no ``jti``) continue to verify so the
rollout is non-disruptive; they cannot be revoked individually
(the operator's lever for a leaked legacy token is secret rotation
per REQ_SDD_TOK_003).

REQ refs: REQ_F_ACC_010 + REQ_SDD_ACC_007 (account-id claim);
REQ_S_KS_009 (operator-signed recovery); REQ_F_TOK_001..005 +
REQ_SDD_TOK_001..005 (CR-024); REQ_NF_TOK_001 (structured audit);
REQ_SDD_PER_005 (raw secret never persisted; only hashes).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from trading_system.observability import structured_log
from trading_system.result import Nothing, Option, Some


HOUSEHOLD_CLAIM: str = "household"

# Logger consumed by the SECURITY-category audit entries
# (REQ_NF_TOK_001).
_AUDIT_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class RevocationLookup(Protocol):
    """The minimal surface ``AccountScopedTokenVerifier`` needs to
    consult a persisted revocation list (CR-024 / REQ_SDD_TOK_002).

    Implemented by:
    - ``trading_system.persistence.repositories.token_revocations.
      OperatorTokenRevocationRepository.is_revoked`` (returns
      ``Result[bool, str]`` — the verifier treats Err as "not
      revoked" to fail-open on a transient DB error; the
      higher-priority signal is the signature check which a
      transient DB error doesn't compromise).
    - In-memory stubs (tests) returning ``bool`` directly.

    The verifier accepts both shapes by inspecting the return type
    in ``_is_jti_revoked``.
    """

    def is_revoked(self, *, account_id: str, jti: str) -> object: ...


@dataclass(frozen=True, slots=True)
class _ParsedToken:
    """Internal — every field needed for verify + lifecycle audit."""

    timestamp_str: str
    account_id: str
    jti: str | None  # None for legacy three-segment tokens
    signature: str
    raw: str

    @property
    def signed_payload(self) -> str:
        if self.jti is None:
            return f"{self.timestamp_str}:{self.account_id}"
        return f"{self.timestamp_str}:{self.account_id}:{self.jti}"


def _parse_token(token: str) -> _ParsedToken | None:
    """Parse a token into its four (or legacy three) segments. ISO
    timestamps contain colons (``HH:MM:SS``) so we rsplit from the
    right and treat the head as the timestamp."""
    if not isinstance(token, str) or not token:
        return None
    # Try four-segment first: timestamp:account:jti:sig.
    parts = token.rsplit(":", 3)
    if len(parts) == 4:
        timestamp_str, account_id, jti, signature = parts
        if not timestamp_str or not account_id or not jti or not signature:
            # If any segment is empty the legacy parser will catch
            # the right shape; fall through.
            pass
        elif _looks_like_jti(jti):
            return _ParsedToken(
                timestamp_str=timestamp_str,
                account_id=account_id,
                jti=jti,
                signature=signature,
                raw=token,
            )
    # Legacy three-segment form.
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return None
    timestamp_str, account_id, signature = parts
    if not timestamp_str or not account_id or not signature:
        return None
    return _ParsedToken(
        timestamp_str=timestamp_str,
        account_id=account_id,
        jti=None,
        signature=signature,
        raw=token,
    )


def _looks_like_jti(s: str) -> bool:
    """A ``uuid4().hex`` is 32 lowercase hex chars. We use this as
    the disambiguator between the four-segment and legacy
    three-segment formats (the third segment is the signature in
    legacy mode, which is 64 lowercase hex chars — distinct
    length)."""
    return len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def _token_hash(raw: str) -> str:
    """SHA-256 of the raw token, used for cross-reference with
    persisted audit rows (REQ_SDD_PER_005 discipline; the raw
    token is never persisted)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AccountScopedTokenVerifier:
    """HMAC-SHA256 verifier with an account-id claim + CR-024
    lifecycle hooks.

    Construction:
    - ``secret`` — current operator secret (required).
    - ``ttl_seconds`` — token lifetime (default 300 s).
    - ``previous_secret`` — optional grace-window secret; tokens
      signed by either ``secret`` OR ``previous_secret`` SHALL
      verify, but ``issue()`` SHALL only ever sign with
      ``secret``. Setting + clearing happens via
      ``rotate_secret(new_secret)`` for atomic flips
      (REQ_SDD_TOK_003).
    - ``revocation_lookup`` — optional ``RevocationLookup``
      (REQ_F_TOK_002). When provided, ``verify()`` checks the
      revocation list BEFORE the TTL check.
    - ``warn_below_seconds`` — operator-facing knob for the
      dashboard's expiry banner; default 60 s (REQ_F_TOK_004).
    """

    secret: bytes
    ttl_seconds: int = 300
    previous_secret: bytes | None = None
    revocation_lookup: RevocationLookup | None = None
    warn_below_seconds: int = 60
    # Injectable for tests; the default SHALL return a tz-aware
    # ``datetime`` so the subtraction in ``verify()`` doesn't trip
    # when the issued token's timestamp carries an offset.
    _clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError(
                "AccountScopedTokenVerifier.secret must be non-empty"
            )
        if self.ttl_seconds <= 0:
            raise ValueError(
                "AccountScopedTokenVerifier.ttl_seconds must be > 0, "
                f"got {self.ttl_seconds}"
            )
        if self.warn_below_seconds < 0:
            raise ValueError(
                "AccountScopedTokenVerifier.warn_below_seconds must be >= 0, "
                f"got {self.warn_below_seconds}"
            )

    # ------------------------------------------------------------------
    # Issue + verify
    # ------------------------------------------------------------------

    def issue(self, *, account_id: str, now: datetime) -> str:
        """Generate a fresh four-segment token (REQ_F_TOK_001). The
        ``jti`` is a ``uuid4().hex`` so revocation has a stable
        handle (REQ_F_TOK_002)."""
        if not account_id.strip():
            raise ValueError(
                "AccountScopedTokenVerifier.issue: account_id must be non-empty"
            )
        timestamp = now.isoformat()
        jti = uuid.uuid4().hex
        signature = self._sign(self.secret, timestamp, account_id, jti)
        token = f"{timestamp}:{account_id}:{jti}:{signature}"
        self._audit(
            event="issue",
            account_id=account_id,
            jti=jti,
            outcome="ok",
            token_hash=_token_hash(token),
        )
        return token

    def verify(self, token: str, *, account_id: str) -> bool:
        """Verify a token AND check its embedded ``account_id`` claim
        matches the targeted account.

        Lifecycle:
        1. Parse (four-segment or legacy three-segment).
        2. account_id claim check.
        3. Revocation check (only when ``jti`` is present).
        4. Signature check against ``secret``, then ``previous_secret``.
        5. TTL check.

        Every outcome emits a SECURITY structured-log line
        (REQ_NF_TOK_001).
        """
        parsed = _parse_token(token)
        if parsed is None:
            self._audit(
                event="verify_failed",
                account_id=account_id,
                jti=None,
                outcome="bad_format",
                token_hash=_token_hash(token) if token else "",
            )
            return False
        if parsed.account_id != account_id:
            self._audit(
                event="verify_failed",
                account_id=account_id,
                jti=parsed.jti,
                outcome="account_mismatch",
                token_hash=_token_hash(token),
            )
            return False
        if parsed.jti is not None and self._is_jti_revoked(
            account_id=parsed.account_id, jti=parsed.jti
        ):
            self._audit(
                event="verify_failed",
                account_id=account_id,
                jti=parsed.jti,
                outcome="revoked",
                token_hash=_token_hash(token),
            )
            return False
        if not self._signature_ok(parsed):
            self._audit(
                event="verify_failed",
                account_id=account_id,
                jti=parsed.jti,
                outcome="bad_signature",
                token_hash=_token_hash(token),
            )
            return False
        if not self._ttl_ok(parsed):
            self._audit(
                event="verify_failed",
                account_id=account_id,
                jti=parsed.jti,
                outcome="expired",
                token_hash=_token_hash(token),
            )
            return False
        self._audit(
            event="verify_ok",
            account_id=account_id,
            jti=parsed.jti,
            outcome="ok",
            token_hash=_token_hash(token),
        )
        return True

    # ------------------------------------------------------------------
    # CR-024 — multi-secret rotation
    # ------------------------------------------------------------------

    def rotate_secret(self, new_secret: bytes) -> None:
        """Atomically rotate the operator secret (REQ_F_TOK_003).

        Moves ``secret`` into ``previous_secret`` and installs
        ``new_secret`` as the active ``secret``. The previous
        ``previous_secret`` is discarded (one grace generation
        only).
        """
        if not new_secret:
            raise ValueError(
                "AccountScopedTokenVerifier.rotate_secret: "
                "new_secret must be non-empty"
            )
        self.previous_secret = self.secret
        self.secret = new_secret
        self._audit(
            event="rotate_secret",
            account_id="",
            jti=None,
            outcome="ok",
        )

    # ------------------------------------------------------------------
    # CR-024 — expiry-warning accessor
    # ------------------------------------------------------------------

    def seconds_until_expiry(self, token: str) -> Option[int]:
        """Return the remaining lifetime in seconds for a verified
        token (REQ_F_TOK_004). ``Nothing()`` for malformed /
        tampered / expired / revoked tokens.

        This accessor is read-only — it SHALL NOT emit a SECURITY
        structured-log entry (REQ_SDD_TOK_004).
        """
        parsed = _parse_token(token)
        if parsed is None:
            return Nothing()
        if parsed.jti is not None and self._is_jti_revoked(
            account_id=parsed.account_id, jti=parsed.jti
        ):
            return Nothing()
        if not self._signature_ok(parsed):
            return Nothing()
        try:
            issued_at = datetime.fromisoformat(parsed.timestamp_str)
        except ValueError:
            return Nothing()
        now = self._now()
        if now is None:
            return Nothing()
        elapsed = now - issued_at
        remaining = timedelta(seconds=self.ttl_seconds) - elapsed
        seconds = int(remaining.total_seconds())
        if seconds < 0:
            return Nothing()
        return Some(seconds)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sign(
        self,
        key: bytes,
        timestamp: str,
        account_id: str,
        jti: str | None = None,
    ) -> str:
        """HMAC over the signed payload — three-field for legacy,
        four-field for CR-024 tokens."""
        if jti is None:
            message = f"{timestamp}:{account_id}".encode("utf-8")
        else:
            message = f"{timestamp}:{account_id}:{jti}".encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def _signature_ok(self, parsed: _ParsedToken) -> bool:
        """Try ``secret`` first; on mismatch + when
        ``previous_secret`` is set, try the previous secret
        (REQ_SDD_TOK_003 multi-secret evaluation order)."""
        expected_current = self._sign(
            self.secret, parsed.timestamp_str, parsed.account_id, parsed.jti
        )
        if hmac.compare_digest(expected_current, parsed.signature):
            return True
        if self.previous_secret is not None:
            expected_previous = self._sign(
                self.previous_secret,
                parsed.timestamp_str,
                parsed.account_id,
                parsed.jti,
            )
            if hmac.compare_digest(expected_previous, parsed.signature):
                return True
        return False

    def _ttl_ok(self, parsed: _ParsedToken) -> bool:
        try:
            issued_at = datetime.fromisoformat(parsed.timestamp_str)
        except ValueError:
            return False
        now = self._now()
        if now is None:
            return False
        delta = now - issued_at
        return timedelta(0) <= delta <= timedelta(seconds=self.ttl_seconds)

    def _now(self) -> datetime | None:
        clock = self._clock
        now = clock() if callable(clock) else clock
        if not isinstance(now, datetime):
            return None
        return now

    def _is_jti_revoked(self, *, account_id: str, jti: str) -> bool:
        """Ask the configured ``revocation_lookup``; treat any
        non-True / non-Ok(True) response as "not revoked"
        (fail-open on transient DB errors — REQ_SDD_TOK_002)."""
        if self.revocation_lookup is None:
            return False
        result = self.revocation_lookup.is_revoked(
            account_id=account_id, jti=jti
        )
        # The repository surface returns Result[bool, str]; tests
        # may pass a plain bool. Handle both.
        if isinstance(result, bool):
            return result
        # Result tagged-union: Ok(True) ⇒ revoked.
        if hasattr(result, "is_ok") and result.is_ok():
            value = result.unwrap()
            return bool(value)
        return False

    def _audit(
        self,
        *,
        event: str,
        account_id: str,
        jti: str | None,
        outcome: str,
        token_hash: str = "",
    ) -> None:
        """Emit a SECURITY structured-log entry (REQ_NF_TOK_001).

        The payload SHALL NOT include the raw secret nor the raw
        token — only the ``jti`` (public id by design) and the
        token's SHA-256 hash for cross-reference with persisted
        audit rows.
        """
        payload: dict[str, object] = {
            "event": event,
            "account_id": account_id,
            "outcome": outcome,
        }
        if jti is not None:
            payload["jti"] = jti
        if token_hash:
            payload["token_hash"] = token_hash
        level = (
            logging.INFO
            if outcome in ("ok",)
            else logging.WARNING
        )
        structured_log(
            _AUDIT_LOGGER,
            level,
            "security",
            f"token:{event}",
            **payload,
        )
