"""``IdempotencyStore`` Protocol + ``InMemoryIdempotencyStore``.

REQ_F_WEB_008 — repeated POSTs with the same ``Idempotency-Key``
header SHALL return the prior response byte-identically without
re-executing the mutation. Phase A ships an in-memory backend; the
CR-008 follow-up adds a ``IdempotencyRepository`` over SQLite that
satisfies the same Protocol surface so the route layer doesn't
change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


@runtime_checkable
class IdempotencyStore(Protocol):
    """Storage surface shared by the in-memory + SQLite backends."""

    def lookup(
        self, *, account_id: AccountId, key: str
    ) -> Result[Option[str], str]: ...

    def record(
        self,
        *,
        account_id: AccountId,
        key: str,
        body: str,
        status_code: int,
    ) -> Result[None, str]: ...


@dataclass(frozen=True, slots=True)
class _Entry:
    body: str
    status_code: int
    at: datetime


@dataclass(slots=True)
class InMemoryIdempotencyStore:
    """In-memory backend for v1.

    Entries are keyed on ``(account_id, key)`` so two accounts can
    reuse the same idempotency key without colliding. The TTL is
    enforced lazily — ``lookup`` returns ``Nothing()`` for any entry
    whose age exceeds ``ttl_seconds``, and the entry stays in the
    dict until the next ``record`` call evicts it (good enough for
    v1; CR-008 SQLite adds proper sweep).
    """

    ttl_seconds: int = 86_400  # 24 hours
    _entries: dict[tuple[AccountId, str], _Entry] = field(default_factory=dict)
    # Injectable clock for tests; production uses ``_default_now``.
    now: Callable[[], datetime] = field(
        default_factory=lambda: _default_now
    )

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError(
                f"InMemoryIdempotencyStore.ttl_seconds must be > 0, "
                f"got {self.ttl_seconds}"
            )

    def lookup(
        self, *, account_id: AccountId, key: str
    ) -> Result[Option[str], str]:
        """Return ``Ok(Some(canonical_body))`` for a fresh entry,
        ``Ok(Nothing())`` if absent or expired."""
        composite = (account_id, key)
        entry = self._entries.get(composite)
        if entry is None:
            return Ok(Nothing())
        # Lazy TTL check.
        now = self._wallclock()
        if now - entry.at > timedelta(seconds=self.ttl_seconds):
            del self._entries[composite]
            return Ok(Nothing())
        return Ok(Some(entry.body))

    def record(
        self,
        *,
        account_id: AccountId,
        key: str,
        body: str,
        status_code: int,
    ) -> Result[None, str]:
        if not key.strip():
            return Err("webui:idempotency_bad_key")
        composite = (account_id, key)
        existing = self._entries.get(composite)
        if existing is not None and existing.body != body:
            # Per REQ_F_WEB_008: replaying with the same key SHALL
            # return the original response. A divergent body for a
            # known key is a programmer error — surface it as a
            # categorised Err so the caller can log + drop.
            return Err("webui:idempotency_conflict")
        self._entries[composite] = _Entry(
            body=body, status_code=status_code, at=self._wallclock()
        )
        return Ok(None)

    def status_code_for(
        self, *, account_id: AccountId, key: str
    ) -> int | None:
        """Convenience for routes that need the status code along
        with the body — Phase B may fold this into ``lookup``'s
        return shape, but the Protocol stays stable in v1."""
        entry = self._entries.get((account_id, key))
        if entry is None:
            return None
        return entry.status_code

    def _wallclock(self) -> datetime:
        return self.now()


def _default_now() -> datetime:
    return datetime.now(tz=UTC)
