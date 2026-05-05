"""Recovery conditions and operator token verification.

REQ refs:
- REQ_S_KS_009 — recovery requires drawdown back below threshold +
  system integrity restored + backtests stable + manual operator
  confirmation (cryptographic token).
- REQ_S_KS_007 — manual confirmation should be required even when
  automated conditions are met.
- REQ_SDS_FLO_005 — manual recovery requires an explicit operator
  token validated at the channel boundary.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RecoveryConditions:
    """All four conditions that must be met before a kill-switch can
    be cleared (REQ_S_KS_009)."""

    drawdown_recovered: bool
    integrity_restored: bool
    backtests_stable: bool

    def all_met(self) -> bool:
        return self.drawdown_recovered and self.integrity_restored and self.backtests_stable


@runtime_checkable
class OperatorTokenVerifier(Protocol):
    """Verifies that an operator-supplied token is valid right now.
    Implementations decide the verification mechanism (HMAC, JWT,
    PKI signature). The state manager only calls ``verify(token)``
    and treats any verifier as opaque."""

    def verify(self, token: str) -> bool: ...


@dataclass(slots=True)
class HmacOperatorTokenVerifier:
    """HMAC-SHA256 token verifier with timestamp + TTL.

    Token format: ``<iso_timestamp>:<hex_signature>``. The signature
    is ``hmac_sha256(secret, iso_timestamp)``. A token is valid iff
    the signature matches AND the issued timestamp is within
    ``ttl_seconds`` of ``now()``.

    The secret is held in memory; production should source it from
    a secrets manager and never log it.
    """

    secret: bytes
    ttl_seconds: int = 300
    _clock: object = datetime.now  # injectable for tests

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError("HmacOperatorTokenVerifier.secret must be non-empty")
        if self.ttl_seconds <= 0:
            raise ValueError(
                f"HmacOperatorTokenVerifier.ttl_seconds must be > 0, got {self.ttl_seconds}"
            )

    def issue(self, *, now: datetime) -> str:
        """Generate a fresh token. Provided for tests / operator
        tooling; production may issue tokens out-of-band."""
        timestamp = now.isoformat()
        signature = self._sign(timestamp)
        return f"{timestamp}:{signature}"

    def verify(self, token: str) -> bool:
        if ":" not in token:
            return False
        timestamp_str, _, signature = token.rpartition(":")
        if not timestamp_str or not signature:
            return False
        expected = self._sign(timestamp_str)
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

    def _sign(self, message: str) -> str:
        return hmac.new(self.secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(slots=True)
class AlwaysValidVerifier:
    """Test-only stub. Never use in production."""

    def verify(self, token: str) -> bool:
        return bool(token)


@dataclass(slots=True)
class AlwaysInvalidVerifier:
    """Test-only stub. Useful for negative paths."""

    def verify(self, token: str) -> bool:
        return False
