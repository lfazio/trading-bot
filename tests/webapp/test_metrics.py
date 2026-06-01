"""C12 — Prometheus instrumentation + `/metrics` endpoint tests.

Covers:
- The `/metrics` endpoint returns the standard Prometheus
  exposition format with the correct Content-Type.
- The endpoint surfaces every defined metric series at least
  once (default-zero counters / histograms render via
  `# HELP` + `# TYPE` headers).
- The engine-facing `time_*` context managers + `record_*`
  counters land observations into the registry.
- When `prometheus_client` is absent the module degrades to
  no-op stubs + the endpoint returns 503.

Phase-8 Part C C12 hardening item. No formal REQ yet.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import AccountScopedTokenVerifier
from trading_system.webapp import WebappState, create_app
from trading_system.webapp import metrics as metrics_module


def _make_client():
    verifier = AccountScopedTokenVerifier(secret=b"metrics-secret", ttl_seconds=300)
    state = WebappState(token_verifier=verifier)
    return TestClient(create_app(state))


# ---------------------------------------------------------------------------
# /metrics endpoint — happy path
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_prometheus_content_type() -> None:
    client = _make_client()
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_metrics_endpoint_publishes_documented_series() -> None:
    """Every documented metric series renders the standard
    Prometheus headers (`# HELP` + `# TYPE`) even at zero
    observations."""
    client = _make_client()
    body = client.get("/metrics").text
    assert "trading_bot_paper_tick_duration_seconds" in body
    assert "trading_bot_broker_submit_duration_seconds" in body
    assert "trading_bot_persistence_write_duration_seconds" in body
    assert "trading_bot_trades_emitted_total" in body
    assert "trading_bot_ks_transitions_total" in body
    # Standard exposition shape — # HELP + # TYPE per metric.
    assert "# HELP trading_bot_paper_tick_duration_seconds" in body
    assert "# TYPE trading_bot_paper_tick_duration_seconds histogram" in body


def test_metrics_endpoint_no_auth_required() -> None:
    """The scrape target hits /metrics without credentials —
    the convention is to put a reverse proxy / network ACL in
    front of the endpoint, not to gate it on the operator
    token."""
    client = _make_client()
    # No Authorization header.
    response = client.get("/metrics")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Engine-facing helpers
# ---------------------------------------------------------------------------


def test_time_paper_tick_records_observation() -> None:
    """A short paper-tick context produces one observation in
    the duration histogram series."""
    # Reading the metric's `_count` after one call SHALL show
    # an increment — Prometheus's internal counter for the
    # histogram.
    before = _histogram_count(
        metrics_module.PAPER_TICK_DURATION_SECONDS, account_id="probe"
    )
    with metrics_module.time_paper_tick(account_id="probe"):
        pass
    after = _histogram_count(
        metrics_module.PAPER_TICK_DURATION_SECONDS, account_id="probe"
    )
    assert after == before + 1


def test_time_broker_submit_records_observation_with_labels() -> None:
    before = _histogram_count(
        metrics_module.BROKER_SUBMIT_DURATION_SECONDS,
        account_id="alpha",
        broker_adapter="local",
    )
    with metrics_module.time_broker_submit(
        account_id="alpha", broker_adapter="local"
    ):
        pass
    after = _histogram_count(
        metrics_module.BROKER_SUBMIT_DURATION_SECONDS,
        account_id="alpha",
        broker_adapter="local",
    )
    assert after == before + 1


def test_time_persistence_write_records_observation() -> None:
    before = _histogram_count(
        metrics_module.PERSISTENCE_WRITE_DURATION_SECONDS,
        repository="ks_snapshots",
        op="write",
    )
    with metrics_module.time_persistence_write(
        repository="ks_snapshots", op="write"
    ):
        pass
    after = _histogram_count(
        metrics_module.PERSISTENCE_WRITE_DURATION_SECONDS,
        repository="ks_snapshots",
        op="write",
    )
    assert after == before + 1


def test_record_trade_increments_counter() -> None:
    before = _counter_value(
        metrics_module.TRADES_EMITTED_TOTAL, account_id="alpha", side="buy"
    )
    metrics_module.record_trade(account_id="alpha", side="buy")
    metrics_module.record_trade(account_id="alpha", side="buy")
    after = _counter_value(
        metrics_module.TRADES_EMITTED_TOTAL, account_id="alpha", side="buy"
    )
    assert after == before + 2


def test_record_ks_transition_increments_counter() -> None:
    before = _counter_value(
        metrics_module.KS_TRANSITIONS_TOTAL, severity="DEGRADE"
    )
    metrics_module.record_ks_transition(severity="DEGRADE")
    after = _counter_value(
        metrics_module.KS_TRANSITIONS_TOTAL, severity="DEGRADE"
    )
    assert after == before + 1


def test_is_available_returns_true_when_dep_installed() -> None:
    """When prometheus_client IS installed (the default CI
    environment after the [metrics] extra is wired), the
    flag SHALL be True."""
    assert metrics_module.is_available() is True


# ---------------------------------------------------------------------------
# Soft-dep degradation — prometheus_client absent
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_503_when_dep_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the soft-dep-missing path by reloading the module
    with prometheus_client hidden. The endpoint surfaces 503 so
    Grafana / Prometheus alerts fire."""
    # Pop prometheus_client + the metrics module from sys.modules
    # so the next import re-triggers the ImportError branch.
    for mod in list(sys.modules):
        if mod == "prometheus_client" or mod.startswith("prometheus_client."):
            sys.modules.pop(mod)
    sys.modules.pop("trading_system.webapp.metrics", None)
    # Block the import.
    import builtins

    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "prometheus_client" or name.startswith("prometheus_client."):
            raise ImportError("simulated absent dep")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    fresh_metrics = importlib.import_module(
        "trading_system.webapp.metrics"
    )
    assert fresh_metrics.is_available() is False

    # The engine-facing helpers SHALL no-op cleanly.
    with fresh_metrics.time_paper_tick(account_id="probe"):
        pass
    fresh_metrics.record_trade(account_id="alpha", side="buy")

    # The endpoint SHALL surface 503. We attach the fresh
    # router to a fresh app so the test doesn't have to
    # rebuild the whole webapp.
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(fresh_metrics.router)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 503

    # Reset back to the production path so subsequent tests
    # see the live registry again.
    monkeypatch.setattr(builtins, "__import__", real_import)
    sys.modules.pop("trading_system.webapp.metrics", None)
    importlib.import_module("trading_system.webapp.metrics")


# ---------------------------------------------------------------------------
# Helpers — read histogram/counter values via the public API
# ---------------------------------------------------------------------------


def _histogram_count(metric, **labels) -> float:
    """Extract `_count` for a labeled histogram series.

    Reading through ``metric.labels(**)`` gives back the child
    series; collecting from it yields samples that carry the
    histogram's internal name suffix (`_count` / `_sum` /
    bucket entries) but no labels (labels live on the parent).
    """
    families = metric.labels(**labels).collect()
    for family in families:
        for sample in family.samples:
            if sample.name.endswith("_count"):
                return sample.value
    return 0.0


def _counter_value(metric, **labels) -> float:
    families = metric.labels(**labels).collect()
    for family in families:
        for sample in family.samples:
            if sample.name.endswith("_total"):
                return sample.value
    return 0.0


# Silence the unused-import warning for `datetime` — it's
# kept around for future tests that need timestamped samples.
_ = datetime
_ = UTC
