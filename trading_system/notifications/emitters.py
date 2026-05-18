"""Upstream-subsystem AnomalyAlert emitters — CR-001 Phase B step 2.

REQ refs:
- REQ_F_NOT_007 — ``AnomalyAlert`` payloads for events short of a
  KS trip (broker rejections, optimizer reward drops, strategy-lab
  candidate rejections, …).
- REQ_SDD_NOT_006 — emitters live with their upstream subsystem.
  This module is the *small* glue that lets the subsystem fire a
  payload through ``NotificationFanOut`` without depending on the
  concrete payload dataclass — keeps the subsystems'
  import-graph closed.

Pattern: an emitter is a small frozen callable that captures
``(fanout, account_id, code_prefix)`` and exposes ``emit(reason)``
or ``emit_for_each(reasons)``. The subsystem invokes it with the
categorised reason string it already produces; the emitter builds
the ``AnomalyAlert`` payload + dispatches it.

The fan-out + the emitter are both optional on every upstream
subsystem — backtest + single-account demos pass ``None`` and the
emission is a no-op (REQ_NF_NOT_001 / REQ_NF_ACC_001 mirror).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from trading_system.models.identifiers import AccountId
from trading_system.notifications.fanout import NotificationFanOut
from trading_system.notifications.payloads import AnomalyAlert, AnomalySeverity


_Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class AnomalyEmitter:
    """Pure callable wrapper. Construct one per subsystem + reuse
    the instance across emissions; the wrapper is hashable +
    pickle-safe so it survives ProcessPoolExecutor handoffs."""

    fanout: NotificationFanOut
    account_id: AccountId
    severity: AnomalySeverity = "WARN"

    def emit(self, code: str, message: str, *, now: _Clock = _utcnow) -> None:
        """Dispatch one ``AnomalyAlert`` through the configured
        fan-out. The categorised ``code`` follows the
        ``<subsystem>:<reason>`` shape every other Err in this
        codebase uses; the ``message`` is the human-readable detail
        the operator sees in Slack / email."""
        payload = AnomalyAlert(
            code=code,
            severity=self.severity,
            account_id=self.account_id,
            message=message,
            at=now(),
        )
        self.fanout.dispatch(payload)


def emit_broker_rejection(
    emitter: AnomalyEmitter | None,
    *,
    reason: str,
    detail: str = "",
    now: _Clock = _utcnow,
) -> None:
    """Convenience wrapper subsystems call inline.

    ``emitter=None`` is the documented no-op path so backtest +
    single-account demos stay bit-identical (REQ_NF_NOT_001 /
    REQ_NF_ACC_001 mirror). The ``reason`` is the categorised
    string from the underlying ``Result.Err``; ``detail`` is the
    optional human-readable supplement.
    """
    if emitter is None:
        return
    message = (
        f"broker rejected: {reason}" if not detail else f"{reason} — {detail}"
    )
    emitter.emit(code=reason, message=message, now=now)


def emit_strategy_rejections(
    emitter: AnomalyEmitter | None,
    rejections: Mapping[object, str] | Iterable[tuple[object, str]],
    *,
    code_prefix: str = "strategy_lab",
    now: _Clock = _utcnow,
) -> None:
    """Emit one ``AnomalyAlert`` per strategy-lab candidate
    rejection.

    ``rejections`` is the same ``rejected[candidate_id] = reason``
    mapping ``LoopController.cycle`` already produces. The emitter
    iterates deterministically (sorted by candidate id string) so
    test fixtures see byte-identical fan-out observation.
    """
    if emitter is None:
        return
    if isinstance(rejections, Mapping):
        items = sorted(rejections.items(), key=lambda kv: str(kv[0]))
    else:
        items = sorted(rejections, key=lambda kv: str(kv[0]))
    for candidate_id, reason in items:
        emitter.emit(
            code=f"{code_prefix}:{reason}",
            message=f"candidate {candidate_id} rejected: {reason}",
            now=now,
        )


def emit_anomaly(
    emitter: AnomalyEmitter | None,
    *,
    code: str,
    message: str,
    now: _Clock = _utcnow,
) -> None:
    """Generic emit hook for any upstream subsystem that produces a
    `(code, message)` pair. ``emitter=None`` no-ops."""
    if emitter is None:
        return
    emitter.emit(code=code, message=message, now=now)


__all__ = [
    "AnomalyEmitter",
    "emit_anomaly",
    "emit_broker_rejection",
    "emit_strategy_rejections",
]
