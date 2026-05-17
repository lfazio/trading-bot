"""``AccountScopedTokenVerifier`` — wraps the existing
``HmacOperatorTokenVerifier`` with a per-account claim check
(REQ_F_ACC_010 / REQ_SDD_ACC_007).

Token format: ``<iso_timestamp>:<account_id>:<hex_signature>``. The
signature is ``hmac_sha256(secret, "<iso_timestamp>:<account_id>")``
— the account_id is part of the signed payload so a token issued for
account "alpha" cannot be replayed against account "beta" even if the
secret is the same. The literal sentinel ``account_id="household"``
is the read-only claim consumed by the dashboard / web UI for
household-level views; mutation endpoints SHALL reject it.

REQ refs: REQ_F_ACC_010, REQ_S_KS_009, REQ_SDD_ACC_007,
REQ_SDD_PER_005.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


HOUSEHOLD_CLAIM: str = "household"


@dataclass(slots=True)
class AccountScopedTokenVerifier:
    """HMAC-SHA256 verifier with an account-id claim.

    Mirrors the existing ``HmacOperatorTokenVerifier`` (REQ_S_KS_009)
    so persistence and notifications can share the verifier
    instance; the account-id binding is an extra payload component
    that prevents cross-account replay.
    """

    secret: bytes
    ttl_seconds: int = 300
    # Injectable for tests; the default SHALL return a tz-aware
    # ``datetime`` so the subtraction in ``verify()`` doesn't trip
    # when the issued token's timestamp carries an offset (which
    # operator-tooled ``issue(now=datetime.now(UTC))`` always does).
    _clock: object = staticmethod(lambda: datetime.now(UTC))

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

    def issue(self, *, account_id: str, now: datetime) -> str:
        """Generate a fresh token. Provided for tests + operator
        tooling; production may issue tokens out-of-band."""
        if not account_id.strip():
            raise ValueError(
                "AccountScopedTokenVerifier.issue: account_id must be non-empty"
            )
        timestamp = now.isoformat()
        signature = self._sign(timestamp, account_id)
        return f"{timestamp}:{account_id}:{signature}"

    def verify(self, token: str, *, account_id: str) -> bool:
        """Verify a token AND check its embedded ``account_id`` claim
        matches the targeted account.

        Returns ``False`` for malformed tokens, signature mismatches,
        expired tokens, or account-id-claim mismatches. Read-only
        endpoints SHALL call this with ``account_id=HOUSEHOLD_CLAIM``;
        per-account write endpoints SHALL call with the targeted
        account's id.
        """
        # ISO timestamps contain ``:`` (e.g., ``"12:00:00"``), so we
        # rsplit from the right to isolate the last two segments
        # (account_id + signature) and treat everything before as the
        # timestamp.
        parts = token.rsplit(":", 2)
        if len(parts) != 3:
            return False
        timestamp_str, claimed_account_id, signature = parts
        if not timestamp_str or not claimed_account_id or not signature:
            return False
        if claimed_account_id != account_id:
            return False
        expected = self._sign(timestamp_str, claimed_account_id)
        if not hmac.compare_digest(expected, signature):
            return False
        try:
            issued_at = datetime.fromisoformat(timestamp_str)
        except ValueError:
            return False
        clock = self._clock
        now = clock() if callable(clock) else clock
        if not isinstance(now, datetime):
            return False
        delta = now - issued_at
        return timedelta(0) <= delta <= timedelta(seconds=self.ttl_seconds)

    def _sign(self, timestamp: str, account_id: str) -> str:
        message = f"{timestamp}:{account_id}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()
