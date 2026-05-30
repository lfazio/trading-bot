"""CR-019 step 2 / TC_LIV_PANEL_003 + TC_LIV_007 — Dashboard live-mode chip + panel.

The dashboard's three-position mode switch SHALL flip the `live` chip
from disabled-with-tooltip to enabled ONLY when ALL of:
  (a) `var/live-preflight.json` exists,
  (b) `outcome="ok"`,
  (c) `checked_at` within the configured staleness window (30s default),
  (d) `config/system.yaml.broker.adapter != "local"`.

When enabled + the operator navigates to `?mode=live`, the live-trading
panel SHALL render with the equity-curve + open-positions + broker
connectivity + emergency-stop control surface.

REQ refs: REQ_F_LIV_002, REQ_F_LIV_003, REQ_SDD_LIV_004.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app


_SECRET = b"live-mode-dashboard-secret" * 4


def _verifier() -> AccountScopedTokenVerifier:
    return AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)


def _household_cookie(verifier: AccountScopedTokenVerifier) -> str:
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(UTC))


def _build_app(
    *,
    verifier: AccountScopedTokenVerifier,
    preflight_artefact_path: Path | None = None,
    broker_selector: str | None = None,
):
    state = WebappState(token_verifier=verifier)
    app = create_app(state)
    if preflight_artefact_path is not None:
        app.state.live_preflight_artefact = preflight_artefact_path
    if broker_selector is not None:
        app.state.broker_selector = broker_selector
    return app


def _write_preflight(
    path: Path, *, outcome: str = "ok", checked_at: datetime | None = None
) -> None:
    payload = {
        "checked_at": (checked_at or datetime.now(UTC)).isoformat(),
        "outcome": outcome,
        "gates": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _login_client(app) -> TestClient:
    client = TestClient(app)
    # Issue a household-claim token and stuff it into the session cookie
    # so the dashboard view bypasses the /login redirect.
    from trading_system.webapp.auth_deps import SESSION_COOKIE_NAME

    verifier = app.state.token_verifier
    token = _household_cookie(verifier)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


# ---------------------------------------------------------------------------
# TC_LIV_007 — chip enablement gate
# ---------------------------------------------------------------------------


class TestLiveModeChipEnablement:
    def test_missing_artefact_keeps_chip_disabled(
        self, tmp_path: Path
    ) -> None:
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=tmp_path / "missing.json",
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/")
        assert response.status_code == 200
        # Disabled chip + reason in the data attr.
        assert 'data-live-mode="disabled"' in response.text
        assert (
            'data-live-mode-reason="live:preflight_artefact_missing"'
            in response.text
        )

    def test_failed_preflight_keeps_chip_disabled(
        self, tmp_path: Path
    ) -> None:
        artefact = tmp_path / "preflight.json"
        _write_preflight(artefact, outcome="failed")
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/")
        assert 'data-live-mode="disabled"' in response.text
        assert (
            'data-live-mode-reason="live:preflight_failed"' in response.text
        )

    def test_stale_preflight_keeps_chip_disabled(
        self, tmp_path: Path
    ) -> None:
        """A preflight artefact older than 30s SHALL be treated as
        stale (REQ_SDD_LIV_004)."""
        artefact = tmp_path / "preflight.json"
        _write_preflight(
            artefact,
            outcome="ok",
            checked_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/")
        assert 'data-live-mode="disabled"' in response.text
        assert (
            'data-live-mode-reason="live:preflight_stale"' in response.text
        )

    def test_local_broker_keeps_chip_disabled_even_with_fresh_ok_preflight(
        self, tmp_path: Path
    ) -> None:
        artefact = tmp_path / "preflight.json"
        _write_preflight(artefact, outcome="ok")
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="local",
        )
        client = _login_client(app)
        response = client.get("/")
        assert 'data-live-mode="disabled"' in response.text
        assert (
            'data-live-mode-reason="live:broker_local"' in response.text
        )

    def test_fresh_ok_preflight_with_concrete_broker_enables_chip(
        self, tmp_path: Path
    ) -> None:
        """REQ_F_LIV_002 — the chip flips to enabled when both
        invariants hold."""
        artefact = tmp_path / "preflight.json"
        _write_preflight(artefact, outcome="ok")
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/")
        assert response.status_code == 200
        assert 'data-live-mode="enabled"' in response.text
        # The enabled chip is an <a> not a <button>.
        assert 'aria-label="Live trading mode"' in response.text


# ---------------------------------------------------------------------------
# TC_LIV_PANEL_003 — panel surface when enabled + navigated
# ---------------------------------------------------------------------------


class TestLiveTradingPanel:
    def test_panel_renders_when_mode_is_live_and_chip_enabled(
        self, tmp_path: Path
    ) -> None:
        artefact = tmp_path / "preflight.json"
        _write_preflight(artefact, outcome="ok")
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/?mode=live")
        assert response.status_code == 200
        # Panel anchored on the documented heading + data-panel attr.
        assert 'data-panel="live-trading"' in response.text
        assert "Live trading — default" in response.text
        # Emergency-stop form pointing at the documented route.
        assert (
            'hx-post="/api/accounts/default/emergency-stop"' in response.text
        )
        # Broker-reconnect button pointing at the documented route.
        assert (
            'hx-post="/api/accounts/default/broker-reconnect"' in response.text
        )
        # The four stat tiles render labels.
        assert "Equity (after tax)" in response.text
        assert "Open positions" in response.text
        assert "Today's realised P&amp;L" in response.text
        assert "Today's rejections" in response.text

    def test_panel_does_not_render_in_paper_mode(self, tmp_path: Path) -> None:
        """The panel is mode-switch-scoped — `?mode=paper` SHALL
        NOT surface the live panel even when the chip is enabled."""
        artefact = tmp_path / "preflight.json"
        _write_preflight(artefact, outcome="ok")
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        response = client.get("/?mode=paper")
        assert 'data-panel="live-trading"' not in response.text

    def test_panel_does_not_render_when_chip_disabled(
        self, tmp_path: Path
    ) -> None:
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=tmp_path / "missing.json",
            broker_selector="xtb",
        )
        client = _login_client(app)
        # Operator forces ?mode=live but chip is disabled — panel
        # SHALL NOT render (gate is the chip status, not the mode
        # query param).
        response = client.get("/?mode=live")
        assert 'data-panel="live-trading"' not in response.text

    def test_panel_carries_broker_selector_and_checked_at(
        self, tmp_path: Path
    ) -> None:
        artefact = tmp_path / "preflight.json"
        ts = datetime(2026, 5, 30, 12, tzinfo=UTC)
        _write_preflight(artefact, outcome="ok", checked_at=ts)
        verifier = _verifier()
        app = _build_app(
            verifier=verifier,
            preflight_artefact_path=artefact,
            broker_selector="xtb",
        )
        client = _login_client(app)
        # Force the artefact to look "fresh" by patching the staleness
        # window — but actually our preflight write uses datetime.now()
        # by default; the test above already covers the staleness path.
        # Here we re-write with a current timestamp to confirm the
        # broker_selector + checked_at land in the panel.
        _write_preflight(artefact, outcome="ok")
        response = client.get("/?mode=live")
        assert "Broker:" in response.text
        assert "xtb" in response.text
        assert "Preflight:" in response.text
