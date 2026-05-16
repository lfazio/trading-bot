"""JSON-line formatter + correlation-id binding for stdlib logging.

The schema matches REQ_SDS_CRS_001 exactly:
``{"ts", "category", "corr_id", "payload"}`` plus the convenience
fields ``level`` / ``account_id`` / ``module`` / ``message`` that the
SDD §12 example also shows.

Correlation lives in a ``ContextVar`` so the per-tick
``corr_id`` + ``account_id`` flow without threading them through
every function signature. The ``log_scope`` context manager is the
single entry point — it binds on ``__enter__`` and resets on
``__exit__`` (including exceptions), so re-entrancy is safe.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal


LogCategory = Literal[
    "trade",
    "decision",
    "ks_event",
    "phase_change",
    "improvement_report",
    "error",
    "regime_transition",
    "notification",
    "approval",
    "config",
    "system",
]


# The fixed fallback format the operator sees if json_output=False.
HUMAN_FORMAT: str = "%(asctime)s %(levelname)-7s %(name)s — %(message)s"


@dataclass(frozen=True, slots=True)
class LogContext:
    """Per-tick / per-account correlation context.

    ``corr_id`` SHALL be unique per ``AccountRegistry.tick`` invocation
    (REQ_SDS_CRS_001). ``account_id`` defaults to the CR-006 sentinel
    ``"default"`` so single-account deployments emit the same value
    the persistence layer's ``DEFAULT_ACCOUNT_ID`` carries.
    """

    corr_id: str
    account_id: str = "default"

    def __post_init__(self) -> None:
        if not self.corr_id:
            raise ValueError("LogContext.corr_id must be non-empty")
        if not self.account_id:
            raise ValueError("LogContext.account_id must be non-empty")


_CTX: ContextVar[LogContext | None] = ContextVar(
    "trading_system_log_context", default=None
)


@contextlib.contextmanager
def log_scope(ctx: LogContext) -> Iterator[None]:
    """Bind ``ctx`` for the duration of the ``with`` block.

    Safe to nest — each entry pushes a new value onto the ContextVar
    stack via the returned ``Token``; ``__exit__`` resets to the
    prior value even on exception.
    """
    token: Token[LogContext | None] = _CTX.set(ctx)
    try:
        yield
    finally:
        _CTX.reset(token)


def current_context() -> LogContext | None:
    """Read the current bound context (or ``None`` outside a scope)."""
    return _CTX.get()


class JsonLineFormatter(logging.Formatter):
    """Emit one JSON object per ``LogRecord`` on a single line.

    Resilient to records emitted *without* a structured payload —
    e.g., the stdlib's own ``"Logging error"`` records or third-party
    libraries that call ``logging.getLogger(...).info("...")``
    directly. Those records receive sensible defaults
    (``category="system"`` / empty ``payload``) so the line is still
    valid JSON.
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = _CTX.get()
        payload_raw = getattr(record, "payload", None)
        payload: dict[str, Any] = (
            _coerce(payload_raw) if isinstance(payload_raw, Mapping) else {}
        )
        # Some callers stash a category at record-creation time
        # via the ``extra=`` kwarg; default to "system" otherwise.
        category = str(getattr(record, "category", "system"))
        out = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "category": category,
            "corr_id": ctx.corr_id if ctx is not None else "",
            "account_id": ctx.account_id if ctx is not None else "default",
            "module": record.name,
            "message": record.getMessage(),
            "payload": payload,
        }
        # ``sort_keys=True`` so byte-identical replays under fixed
        # inputs are possible (REQ_NF_DET_001 family).
        return json.dumps(out, separators=(",", ":"), sort_keys=True)


def _coerce(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively make ``payload`` JSON-serialisable.

    ``Decimal`` and ``datetime`` round-trip through ``str()`` /
    ``.isoformat()`` so the JSON envelope preserves exact precision
    (REQ_F_PER_005 family — Decimal-as-TEXT, ISO-8601 datetimes).
    """
    return {k: _coerce_value(v) for k, v in payload.items()}


def _coerce_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Mapping):
        return _coerce(v)
    if isinstance(v, (list, tuple)):  # noqa: UP038 — tuple is a stdlib type, not a generic alias
        return [_coerce_value(x) for x in v]
    return v


def structured_log(
    logger: logging.Logger,
    level: int,
    category: LogCategory,
    message: str,
    /,
    **payload: Any,
) -> None:
    """Emit one structured record on ``logger``.

    Keyword args become the JSON ``payload`` object. Use the
    stdlib level constants (``logging.INFO`` / ``WARNING`` / etc.).
    The current ``LogContext`` (if any) is read at format time, so
    callers do NOT pass ``corr_id`` here.
    """
    logger.log(level, message, extra={"category": category, "payload": payload})


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    stream: io.TextIOBase | None = None,
    replace_handlers: bool = True,
) -> None:
    """One-time root-logger setup.

    ``replace_handlers=True`` (the default) clears existing handlers
    so a repeat call swaps the formatter without doubling output.
    ``stream`` defaults to ``sys.stderr`` to match REQ_NF_LOG_001's
    "logged with timestamps" expectation without polluting the
    demo's stdout (where ``main.py`` prints the human-readable
    summary).
    """
    root = logging.getLogger()
    if replace_handlers:
        for h in list(root.handlers):
            root.removeHandler(h)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    formatter: logging.Formatter = (
        JsonLineFormatter() if json_output else logging.Formatter(HUMAN_FORMAT)
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
