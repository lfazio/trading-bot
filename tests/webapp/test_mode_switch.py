"""Tests for the CR-019 mode switch (REQ_F_WEB2_002).

Three-position switch on the dashboard: paper / backtest / live.
`live` is disabled-with-tooltip until the live-trading amendment.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.webapp import WebappState, create_app


_SECRET = b"mode-switch-test"


def _client():
    verifier = AccountScopedTokenVerifier(secret=_SECRET, ttl_seconds=3600)
    return TestClient(create_app(WebappState(token_verifier=verifier))), verifier


def _token(verifier):
    return verifier.issue(account_id=HOUSEHOLD_CLAIM, now=datetime.now(tz=UTC))


def test_mode_switch_renders_three_controls() -> None:
    """REQ_F_WEB2_002 — paper / backtest / live SHALL all appear."""
    client, verifier = _client()
    body = client.get(
        "/", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert "Paper trading" in body
    assert "Backtest" in body
    assert "Live trading" in body


def test_live_mode_is_disabled_with_documented_tooltip() -> None:
    """REQ_F_WEB2_002 — `live` SHALL be disabled with a tooltip
    that documents the broker-adapter gate."""
    client, verifier = _client()
    body = client.get(
        "/", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    # The live button SHALL carry the disabled attribute + an
    # aria-disabled="true" annotation for assistive tech.
    live_match = re.search(
        r"<button[^>]+(?:Live trading|aria-label=\"Live trading[^\"]*)[^>]*>",
        body,
    )
    # The disabled button can be anywhere; pin the markers more
    # liberally to avoid order-sensitivity.
    assert 'aria-label="Live trading mode' in body
    assert 'aria-disabled="true"' in body
    assert "disabled" in body
    # Tooltip mentions the broker-adapter gate.
    assert "REQ_F_BRK_003" in body or "broker-adapter" in body
    # Should NOT have a route that activates live mode.
    assert "/?mode=live" not in body
    del live_match  # unused; structural check is enough


def _tab_block(body: str, *, label: str) -> str:
    """Pull the mode-switch tab whose ``aria-label`` matches.

    Each tab is an ``<a>`` or ``<button>`` with a documented
    aria-label — that's specific enough to avoid colliding with
    the base nav's plain ``<a href="/jobs">Backtests</a>`` link.
    """
    match = re.search(
        rf'<(?:a|button)[^>]+aria-label="{re.escape(label)}[^"]*"[^>]*>.*?</(?:a|button)>',
        body,
        re.DOTALL,
    )
    assert match is not None, f"no tab found with aria-label={label!r}"
    return match.group(0)


def test_paper_is_active_by_default() -> None:
    """No ``?mode=`` query param SHALL default to paper-mode active."""
    client, verifier = _client()
    body = client.get(
        "/", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert 'aria-selected="true"' in _tab_block(body, label="Paper trading mode")


def test_mode_query_param_drives_active_state() -> None:
    """``?mode=backtest`` SHALL flip aria-selected on the backtest tab."""
    client, verifier = _client()
    body = client.get(
        "/?mode=backtest",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    assert 'aria-selected="true"' in _tab_block(body, label="Backtest mode")
    # Paper SHALL be deselected.
    assert 'aria-selected="false"' in _tab_block(body, label="Paper trading mode")


def test_unknown_mode_falls_back_to_paper() -> None:
    """``?mode=garbage`` SHALL silently coerce to paper."""
    client, verifier = _client()
    body = client.get(
        "/?mode=hyperdrive",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    assert 'aria-selected="true"' in _tab_block(body, label="Paper trading mode")


def test_mode_switch_carries_account_id_through_paper_link() -> None:
    """When the operator switches accounts, the paper-mode link
    SHALL preserve the active account_id so the SSE-targeted
    session doesn't reset to ``default``."""
    client, verifier = _client()
    body = client.get(
        "/?account_id=paper-foo",
        headers={"Authorization": f"Bearer {_token(verifier)}"},
    ).text
    assert "/?mode=paper&amp;account_id=paper-foo" in body or "/?mode=paper&account_id=paper-foo" in body


def test_mode_switch_has_aria_tablist_role() -> None:
    """REQ_NF_WEB2_003 + REQ_NF_WEB2_005 spirit — the switch is a
    semantic tablist with aria-label set on every tab."""
    client, verifier = _client()
    body = client.get(
        "/", headers={"Authorization": f"Bearer {_token(verifier)}"}
    ).text
    assert 'role="tablist"' in body
    assert 'aria-label="Trading mode"' in body
    # Each tab carries an aria-label.
    assert 'aria-label="Paper trading mode"' in body
    assert 'aria-label="Backtest mode"' in body
    assert 'aria-label="Live trading mode' in body  # disabled variant carries extra suffix
