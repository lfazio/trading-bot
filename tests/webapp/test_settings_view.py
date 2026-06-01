"""CR-032 — operator settings view tests.

Cover TC_SET_001..006 from the cascade.

REQ refs: REQ_F_SET_001..005, REQ_NF_SET_001, REQ_SDD_SET_001..004.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_CONFIG_DIR = _REPO_ROOT / "config"


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the bundled `notifications.yaml` into ``tmp_path`` +
    point ``TRADING_BOT_CONFIG_DIR`` at it so the settings view's
    save handler writes into the test's temp directory."""
    cd = tmp_path / "config"
    cd.mkdir()
    shutil.copy(
        _BUNDLED_CONFIG_DIR / "notifications.yaml",
        cd / "notifications.yaml",
    )
    monkeypatch.setenv("TRADING_BOT_CONFIG_DIR", str(cd))
    return cd


def _make_client():
    verifier = AccountScopedTokenVerifier(secret=b"settings-secret", ttl_seconds=300)
    state = WebappState(token_verifier=verifier)
    app = create_app(state)
    return TestClient(app), verifier, app


def _household_token(verifier: AccountScopedTokenVerifier) -> str:
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


# ---------------------------------------------------------------------------
# TC_SET_001 — view rendering + auth
# ---------------------------------------------------------------------------


def test_settings_landing_redirects_to_notifications(
    isolated_config_dir: Path,
) -> None:
    """`GET /operator/settings` redirects to the v1 wedge
    notifications sub-page."""
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/operator/settings",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/operator/settings/notifications"


def test_settings_landing_redirects_unauth_to_login(
    isolated_config_dir: Path,
) -> None:
    client, _, _ = _make_client()
    response = client.get(
        "/operator/settings", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_notifications_form_renders_with_loaded_config(
    isolated_config_dir: Path,
) -> None:
    """REQ_F_SET_001 / REQ_F_SET_002 — form pre-filled from
    the on-disk YAML; household-claim is ACCEPTED (settings
    household-scoped)."""
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.text
    # Form sections present.
    assert 'id="settings-channels"' in body
    assert 'id="settings-retry"' in body
    assert 'id="settings-approval"' in body
    assert 'id="settings-slack"' in body
    assert 'id="settings-email"' in body
    # Save button.
    assert 'type="submit"' in body


def test_notifications_form_per_account_token_also_accepted(
    isolated_config_dir: Path,
) -> None:
    """REQ_F_SET_001 — per-account tokens are also accepted
    on read (settings are household-scoped but per-account is
    permissive)."""
    client, verifier, _ = _make_client()
    token = verifier.issue(account_id="paper-alpha", now=datetime.now(tz=UTC))
    response = client.get(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# TC_SET_002 — save round-trip
# ---------------------------------------------------------------------------


def test_save_writes_yaml_and_loader_re_reads_value(
    isolated_config_dir: Path,
) -> None:
    """POST /operator/settings/notifications writes the YAML
    atomically + the loader picks up the new value on re-read."""
    from trading_system.notifications.loader import load_notifications_config

    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.post(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "channels": ["local_log"],
            "retry.max_attempts": "5",
            "retry.base_delay_seconds": "0.10",
            "retry.growth_factor": "1.5",
            "approval.timeout_seconds": "30",
            "approval.threshold_amount": "100.00",
            "approval.threshold_currency": "EUR",
            "local_log_path": "var/logs/notifications.jsonl",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    # Round-trip: re-load + assert the new values landed.
    from trading_system.result import Ok

    rt = load_notifications_config(
        isolated_config_dir / "notifications.yaml"
    )
    assert isinstance(rt, Ok), rt
    cfg = rt.value
    assert cfg.retry.max_attempts == 5
    assert cfg.retry.base_delay_seconds == 0.10
    assert cfg.retry.growth_factor == 1.5
    assert cfg.approval.timeout_seconds == 30


# ---------------------------------------------------------------------------
# TC_SET_003 — validation failure
# ---------------------------------------------------------------------------


def test_invalid_payload_redirects_with_error_and_preserves_yaml(
    isolated_config_dir: Path,
) -> None:
    """REQ_F_SET_003 — invalid value (max_attempts=0) redirects
    back to the form with the categorised Err; the on-disk
    YAML SHALL remain unchanged."""
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    yaml_path = isolated_config_dir / "notifications.yaml"
    before_bytes = yaml_path.read_bytes()
    response = client.post(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "channels": ["local_log"],
            "retry.max_attempts": "0",  # invariant violation
            "retry.base_delay_seconds": "0.05",
            "retry.growth_factor": "2.0",
            "approval.timeout_seconds": "60",
            "approval.threshold_amount": "0",
            "approval.threshold_currency": "EUR",
            "local_log_path": "var/logs/notifications.jsonl",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error_field=retry" in response.headers["location"]
    # File is untouched.
    after_bytes = yaml_path.read_bytes()
    assert before_bytes == after_bytes


# ---------------------------------------------------------------------------
# TC_SET_004 — reload-pending banner
# ---------------------------------------------------------------------------


def test_successful_save_sets_reload_pending(
    isolated_config_dir: Path,
) -> None:
    """REQ_F_SET_004 — after a successful save, ``app.state.
    reload_pending`` SHALL be a populated ``ReloadPending``."""
    from trading_system.webapp.settings_state import ReloadPending

    client, verifier, app = _make_client()
    token = _household_token(verifier)
    assert app.state.reload_pending is None
    client.post(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "channels": ["local_log"],
            "retry.max_attempts": "3",
            "retry.base_delay_seconds": "0.05",
            "retry.growth_factor": "2.0",
            "approval.timeout_seconds": "60",
            "approval.threshold_amount": "0",
            "approval.threshold_currency": "EUR",
            "local_log_path": "var/logs/notifications.jsonl",
        },
        follow_redirects=False,
    )
    rp = app.state.reload_pending
    assert isinstance(rp, ReloadPending)
    assert "retry" in rp.sections_changed
    assert "channels" in rp.sections_changed


def test_fresh_app_has_no_reload_pending(
    isolated_config_dir: Path,
) -> None:
    """Restart IS the reload — a fresh `create_app()` SHALL
    NOT carry a reload-pending state."""
    _, _, app = _make_client()
    assert app.state.reload_pending is None


# ---------------------------------------------------------------------------
# TC_SET_005 — user-menu chrome
# ---------------------------------------------------------------------------


def test_dashboard_chrome_carries_user_menu_dropdown() -> None:
    """REQ_F_SET_005 — the dashboard chrome SHALL render the
    user-menu dropdown with Settings + Log out + About entries
    + the documented `aria-label` for the no-op logout button."""
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    # Use the login page to exercise the chrome (any chrome-
    # rendering route works — onboarding is a clean one).
    response = client.get(
        "/onboarding",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    # Either 200 (logged in) or another redirect; we just need
    # the chrome to render somewhere. Use /onboarding which is
    # an auth-gated browser view.
    if response.status_code != 200:
        response = client.get("/")  # try the dashboard landing
    body = response.text
    # User menu trigger.
    assert "user-menu" in body
    # Three entries — Jinja's whitespace control inserts
    # newlines around button-label text so the literal
    # ">About<" doesn't appear; substring match instead.
    assert ">Settings<" in body  # <a> link — tight markup
    assert "Log out" in body
    assert "About" in body
    assert 'aria-label="About this build"' in body
    # Logout aria-label per CR-032 question 3.
    assert (
        "logout requires clearing the browser cookie"
        in body
    )


# ---------------------------------------------------------------------------
# TC_SET_006 — secret discipline
# ---------------------------------------------------------------------------


def test_yaml_carries_env_var_name_not_resolved_value(
    isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ_NF_SET_001 — saving with `slack.webhook_url_env =
    PROD_SLACK_URL` writes the NAME to disk, never the
    resolved value."""
    monkeypatch.setenv("PROD_SLACK_URL", "https://example.com/secret-webhook")
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.post(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "channels": ["local_log", "slack"],
            "retry.max_attempts": "3",
            "retry.base_delay_seconds": "0.05",
            "retry.growth_factor": "2.0",
            "approval.timeout_seconds": "60",
            "approval.threshold_amount": "0",
            "approval.threshold_currency": "EUR",
            "local_log_path": "var/logs/notifications.jsonl",
            "slack.webhook_url_env": "PROD_SLACK_URL",
            "slack.timeout_seconds": "5.0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    body = (isolated_config_dir / "notifications.yaml").read_text()
    # The NAME is written.
    assert "PROD_SLACK_URL" in body
    # The resolved VALUE is NEVER written.
    assert "example.com/secret-webhook" not in body


def test_env_var_indicator_renders_status_without_value(
    isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ_NF_SET_001 — the env-var status indicator renders
    'set' / 'unset' but NEVER the value itself."""
    monkeypatch.setenv("PROD_WEBHOOK", "https://secret/url")
    # Pre-save: write a config that references PROD_WEBHOOK so
    # the form-render path surfaces the indicator.
    yaml_path = isolated_config_dir / "notifications.yaml"
    yaml_path.write_text(
        """
notifications:
  channels: [local_log, slack]
  slack:
    webhook_url_env: PROD_WEBHOOK
""",
        encoding="utf-8",
    )
    client, verifier, _ = _make_client()
    token = _household_token(verifier)
    response = client.get(
        "/operator/settings/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.text
    # Env-var name appears.
    assert "PROD_WEBHOOK" in body
    # Set/unset indicator appears.
    assert ">set<" in body
    # The resolved value NEVER appears.
    assert "secret/url" not in body
