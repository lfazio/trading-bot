"""C12 — Prometheus instrumentation + `/metrics` endpoint.

Exposes the standard Prometheus exposition format at
``GET /metrics`` so an operator's scrape target (Grafana
Agent / Prometheus server / Otel collector) can pull the
runtime's per-tick / per-submit / per-write latencies plus
a small set of counters tracking trade flow + KS state
transitions.

``prometheus_client`` is a **soft dependency** — the
metrics module degrades to no-op stubs when the library
isn't installed so deployments that don't want the metrics
surface (or are running the minimal stdlib `webui/`
fallback) don't have to install the dependency. The
``/metrics`` endpoint returns ``503`` when the soft dep
is missing so the scrape target's "endpoint down" alerting
fires correctly.

Public surface:

- ``PAPER_TICK_DURATION_SECONDS`` (Histogram) — paper-trading
  runtime's tick latency in seconds.
- ``BROKER_SUBMIT_DURATION_SECONDS`` (Histogram) — broker
  submit latency in seconds.
- ``PERSISTENCE_WRITE_DURATION_SECONDS`` (Histogram) — labeled
  by repository / op.
- ``TRADES_EMITTED_TOTAL`` (Counter) — labeled by account_id.
- ``KS_TRANSITIONS_TOTAL`` (Counter) — labeled by severity.

Each metric is exposed via a module-level singleton + a
``time_*`` context manager that callers can use to record a
duration without importing prometheus_client directly.
Engine modules consume the context manager only; the
prometheus_client import stays in this file.

Phase-8 Part C C12 hardening item — operator-visible
observability. The C12 surface is documented in
`Documentations/Feature-Gap-Analysis-2026-05-23.md`. No
formal SRS REQ yet; a follow-up CR cascade can promote the
metric vocabulary into REQ_NF_OBS_xxx when operator usage
informs the contract.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Response


_LOG = logging.getLogger(__name__)


try:  # noqa: SIM105 — explicit branch so the rest of the module can read prometheus_client + the missing-dep flag
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
    )
    from prometheus_client.exposition import CONTENT_TYPE_LATEST

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised when the soft dep is absent
    _PROMETHEUS_AVAILABLE = False
    CollectorRegistry = None  # type: ignore[assignment,misc]
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]
    generate_latest = None  # type: ignore[assignment]
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# Metric definitions — module-level singletons against the default registry
# so two `import metrics` calls share the same series.
# ---------------------------------------------------------------------------


# Histogram bucket families tuned for the documented latency
# bands:
#   - paper tick: 1ms - 5s (yfinance fetch dominates)
#   - broker submit: 0.5ms - 1s
#   - persistence write: 0.1ms - 200ms (SQLite WAL)
_PAPER_TICK_BUCKETS = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0
)
_BROKER_SUBMIT_BUCKETS = (
    0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0
)
_PERSISTENCE_WRITE_BUCKETS = (
    0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2
)


if _PROMETHEUS_AVAILABLE:
    PAPER_TICK_DURATION_SECONDS = Histogram(
        "trading_bot_paper_tick_duration_seconds",
        "Wall-clock latency of one paper-trading runtime tick "
        "(broker tick + portfolio mark + strategy evaluate + "
        "submit + equity record).",
        labelnames=("account_id",),
        buckets=_PAPER_TICK_BUCKETS,
    )
    BROKER_SUBMIT_DURATION_SECONDS = Histogram(
        "trading_bot_broker_submit_duration_seconds",
        "Wall-clock latency of one broker.submit call from the "
        "paper / live runtime.",
        labelnames=("account_id", "broker_adapter"),
        buckets=_BROKER_SUBMIT_BUCKETS,
    )
    PERSISTENCE_WRITE_DURATION_SECONDS = Histogram(
        "trading_bot_persistence_write_duration_seconds",
        "Wall-clock latency of one persistence-layer write "
        "(BEGIN IMMEDIATE → COMMIT). Labeled by repository + "
        "operation.",
        labelnames=("repository", "op"),
        buckets=_PERSISTENCE_WRITE_BUCKETS,
    )
    TRADES_EMITTED_TOTAL = Counter(
        "trading_bot_trades_emitted_total",
        "Cumulative count of trades emitted by the runtime, "
        "labeled by account_id + side (buy/sell).",
        labelnames=("account_id", "side"),
    )
    KS_TRANSITIONS_TOTAL = Counter(
        "trading_bot_ks_transitions_total",
        "Cumulative count of kill-switch state transitions, "
        "labeled by severity (DEGRADE / KILL / RECOVERY).",
        labelnames=("severity",),
    )
else:
    PAPER_TICK_DURATION_SECONDS = None
    BROKER_SUBMIT_DURATION_SECONDS = None
    PERSISTENCE_WRITE_DURATION_SECONDS = None
    TRADES_EMITTED_TOTAL = None
    KS_TRANSITIONS_TOTAL = None


def is_available() -> bool:
    """Return whether the soft dep `prometheus_client` is installed."""
    return _PROMETHEUS_AVAILABLE


# ---------------------------------------------------------------------------
# Engine-facing helpers — no prometheus_client import outside this file.
# ---------------------------------------------------------------------------


@contextmanager
def time_paper_tick(*, account_id: str):
    """Record the wall-clock duration of one paper-trading tick.

    Usage::

        with metrics.time_paper_tick(account_id=str(session.account_id)):
            self._apply_bar(bar)

    No-op when ``prometheus_client`` isn't installed — the
    runtime keeps working without observability.
    """
    if PAPER_TICK_DURATION_SECONDS is None:
        yield
        return
    with PAPER_TICK_DURATION_SECONDS.labels(account_id=account_id).time():
        yield


@contextmanager
def time_broker_submit(*, account_id: str, broker_adapter: str):
    """Record the wall-clock duration of one broker.submit call."""
    if BROKER_SUBMIT_DURATION_SECONDS is None:
        yield
        return
    with BROKER_SUBMIT_DURATION_SECONDS.labels(
        account_id=account_id, broker_adapter=broker_adapter
    ).time():
        yield


@contextmanager
def time_persistence_write(*, repository: str, op: str):
    """Record the wall-clock duration of one persistence write
    (BEGIN IMMEDIATE → COMMIT)."""
    if PERSISTENCE_WRITE_DURATION_SECONDS is None:
        yield
        return
    with PERSISTENCE_WRITE_DURATION_SECONDS.labels(
        repository=repository, op=op
    ).time():
        yield


def record_trade(*, account_id: str, side: str) -> None:
    """Increment the trades-emitted counter for one trade."""
    if TRADES_EMITTED_TOTAL is None:
        return
    TRADES_EMITTED_TOTAL.labels(account_id=account_id, side=side).inc()


def record_ks_transition(*, severity: str) -> None:
    """Increment the KS-transition counter for one state change."""
    if KS_TRANSITIONS_TOTAL is None:
        return
    KS_TRANSITIONS_TOTAL.labels(severity=severity).inc()


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


router = APIRouter()


@router.get("/metrics", response_class=Response, name="metrics-endpoint")
def get_metrics() -> Response:
    """Prometheus exposition endpoint (Phase-8 Part C C12).

    Returns ``200 text/plain; version=0.0.4`` with the current
    metric values on success; ``503`` when the soft dep
    ``prometheus_client`` isn't installed so the scrape
    target's downtime alerting fires.

    No authentication — the convention is for operators to
    expose this endpoint on an internal-only network or
    behind a reverse proxy. The webapp's existing
    auth-gated panels handle the operator UI; ``/metrics``
    is for machine scraping.
    """
    if not _PROMETHEUS_AVAILABLE:
        return Response(
            content=(
                "# prometheus_client not installed; metrics endpoint "
                "is in degraded mode\n"
            ),
            media_type=CONTENT_TYPE_LATEST,
            status_code=503,
        )
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


def __getattr__(name: str) -> Any:
    """Surface a clear error when callers access a metric that
    isn't exported yet, instead of an opaque ``AttributeError``."""
    raise AttributeError(
        f"trading_system.webapp.metrics has no attribute {name!r}"
    )
