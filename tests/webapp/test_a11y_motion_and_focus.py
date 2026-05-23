"""Accessibility audits for prefers-reduced-motion + focus-trap.

REQ refs:
- REQ_NF_WEB2_004 — `prefers-reduced-motion: reduce` SHALL
  suppress UI animations; the SSE channel stays event-driven
  (already EventSource).
- REQ_SDD_WEB2_008 — the documented CSS block lives in
  ``static/app.css``.
- REQ_SDD_WEB2_007 — focus-trap helper for modal dialogs
  (role="dialog" + aria-modal="true") ships inline in base.html,
  re-arms on HTMX swaps via MutationObserver.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_CSS = _REPO_ROOT / "trading_system" / "webapp" / "static" / "app.css"
_BASE_HTML = (
    _REPO_ROOT / "trading_system" / "webapp" / "templates" / "base.html"
)
_ONBOARDING_HTML = (
    _REPO_ROOT / "trading_system" / "webapp" / "templates" / "onboarding.html"
)


# ---------------------------------------------------------------------------
# REQ_NF_WEB2_004 + REQ_SDD_WEB2_008 — reduced-motion CSS block
# ---------------------------------------------------------------------------


def test_app_css_has_prefers_reduced_motion_block() -> None:
    """REQ_SDD_WEB2_008 — the documented CSS block SHALL live in
    ``static/app.css``."""
    text = _APP_CSS.read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in text


def test_reduced_motion_block_suppresses_animations_and_transitions() -> None:
    """REQ_NF_WEB2_004 — the block SHALL collapse animation +
    transition durations to ~0 so operators with the OS preference
    set get instant state changes."""
    text = _APP_CSS.read_text(encoding="utf-8")
    block_match = re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([^}]*\{[^}]*\}[^}]*)*\}",
        text,
        re.DOTALL,
    )
    assert block_match is not None
    block = block_match.group(0)
    # Both animation-duration AND transition-duration SHALL be
    # collapsed inside the block.
    assert "animation-duration" in block
    assert "transition-duration" in block
    # The values SHALL be small (the documented 0.001ms pattern).
    assert "0.001ms" in block


# ---------------------------------------------------------------------------
# REQ_SDD_WEB2_007 — focus-trap helper
# ---------------------------------------------------------------------------


def test_base_html_ships_focus_trap_helper_inline() -> None:
    """REQ_SDD_WEB2_007 — base.html SHALL inline the focus-trap
    helper so every page in the webapp arms it."""
    body = _BASE_HTML.read_text(encoding="utf-8")
    # Heuristic markers — the helper exposes a few distinctive
    # tokens that a refactor must preserve.
    assert "armAllDialogs" in body
    assert "role=\"dialog\"" in body or "role=&quot;dialog&quot;" in body
    assert "aria-modal=\"true\"" in body or "aria-modal=&quot;true&quot;" in body


def test_focus_trap_handles_tab_and_shift_tab_wrap() -> None:
    """The helper SHALL document the Tab + Shift-Tab wrapping
    logic (forward-tab at the last element wraps to the first,
    Shift-Tab at the first wraps to the last)."""
    body = _BASE_HTML.read_text(encoding="utf-8")
    # The helper uses `e.shiftKey` + `document.activeElement` to
    # detect the boundaries; pin both markers so a refactor
    # can't silently drop the wrap logic.
    assert "shiftKey" in body
    assert "activeElement" in body


def test_focus_trap_handles_escape_when_dialog_opts_in() -> None:
    """REQ_SDD_WEB2_007 — Esc SHALL release focus to the dialog's
    trigger ONLY when the dialog declares
    ``data-close-on-esc="true"``. Helpers without the opt-in are
    unchanged."""
    body = _BASE_HTML.read_text(encoding="utf-8")
    assert "data-close-on-esc" in body
    assert "Escape" in body


def test_focus_trap_re_arms_on_htmx_swap_via_mutation_observer() -> None:
    """The helper SHALL re-arm on HTMX swaps so a dialog
    inserted dynamically still traps focus."""
    body = _BASE_HTML.read_text(encoding="utf-8")
    assert "MutationObserver" in body
    assert "childList" in body
    assert "subtree" in body


def test_onboarding_dialog_opts_into_close_on_esc() -> None:
    """The onboarding wizard SHALL declare ``data-close-on-esc``
    so Esc returns the operator to the trigger element."""
    body = _ONBOARDING_HTML.read_text(encoding="utf-8")
    assert 'data-close-on-esc="true"' in body
    # And the dialog still carries the documented modal markup.
    assert 'role="dialog"' in body
    assert 'aria-modal="true"' in body
    assert 'aria-labelledby="onboarding-heading"' in body


def test_focus_trap_guards_against_double_arming() -> None:
    """The helper SHALL set ``data-focus-trap-armed`` so the
    MutationObserver re-arm pass doesn't double-bind the
    keydown handler."""
    body = _BASE_HTML.read_text(encoding="utf-8")
    assert "focusTrapArmed" in body or "focus-trap-armed" in body
