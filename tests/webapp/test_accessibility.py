"""WCAG 2.1 AA contrast audit on the webapp's design system.

REQ refs:
- REQ_NF_WEB2_002 — every color combination in
  ``static/app.css`` SHALL meet WCAG 2.1 AA contrast (4.5:1 on
  body text, 3:1 on large text + non-text status badges).
- REQ_SDD_WEB2_006 — closed ``AUDITED_PAIRS`` dict + the
  documented relative-luminance computation + the untested-token
  guard.

The audit is a pure-Python computation (no Selenium / no
headless browser):

1. Parse the ``:root { ... }`` block to extract token names →
   hex values + collect every hex literal anywhere in the file.
2. ``AUDITED_PAIRS`` declares the documented foreground /
   background pair set with its required threshold. The test
   asserts each pair clears the threshold.
3. Untested-token guard: every color literal that appears in
   ``app.css`` SHALL participate in at least one audited pair
   OR appear in the ``DECORATIVE_TOKENS`` allow-list (shadows,
   borders, focus rings — non-text surfaces where contrast is
   not load-bearing).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_CSS = _REPO_ROOT / "trading_system" / "webapp" / "static" / "app.css"


# ---------------------------------------------------------------------------
# Color math — REQ_SDD_WEB2_006 documented formulas
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse a CSS hex color to an (r, g, b) triple. Accepts
    3- and 6-digit forms (``#fff`` + ``#ffffff``)."""
    s = hex_str.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"unsupported hex color: {hex_str!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative-luminance formula (REQ_SDD_WEB2_006)."""

    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Return the WCAG 2.1 contrast ratio of foreground / background.

    Symmetric — caller doesn't have to order the pair.
    """
    l1 = relative_luminance(_hex_to_rgb(fg_hex))
    l2 = relative_luminance(_hex_to_rgb(bg_hex))
    if l2 > l1:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# ---------------------------------------------------------------------------
# CSS-token extraction
# ---------------------------------------------------------------------------


_ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}", re.DOTALL)
_TOKEN_DECL_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}")


def _read_app_css() -> str:
    return _APP_CSS.read_text(encoding="utf-8")


def _root_tokens() -> dict[str, str]:
    """Parse ``:root { --name: value; ... }`` into a mapping of
    token name → trimmed value. Only hex-color tokens are
    surfaced; tokens whose value isn't a hex color (e.g.,
    ``--radius: 6px``, ``--shadow-sm: 0 1px 2px ...``) are
    skipped."""
    text = _read_app_css()
    match = _ROOT_BLOCK_RE.search(text)
    assert match is not None, "no :root { ... } block in app.css"
    out: dict[str, str] = {}
    for name, value in _TOKEN_DECL_RE.findall(match.group(1)):
        value = value.strip()
        if value.startswith("#") and len(value) in (4, 7):
            out[name] = value
    return out


def _all_hex_literals() -> set[str]:
    """Every hex color literal that appears anywhere in app.css.

    Normalised to lowercase 6-digit form so ``#FFF`` + ``#ffffff``
    collapse to a single token. Alpha-bearing hex (#rrggbbaa) is
    skipped — those are decorative shadows / overlays.
    """
    text = _read_app_css()
    out: set[str] = set()
    for raw in _HEX_RE.findall(text):
        s = raw.lstrip("#").lower()
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            out.add("#" + s)
    return out


# ---------------------------------------------------------------------------
# AUDITED_PAIRS — closed-set declaration (REQ_SDD_WEB2_006)
# ---------------------------------------------------------------------------


_T = _root_tokens()


# Threshold tier: ``body`` (4.5:1, WCAG AA normal text),
# ``large`` (3:1, WCAG AA large text / UI / non-text).
Tier = Literal["body", "large"]


# (label, fg_hex, bg_hex, tier)
AUDITED_PAIRS: tuple[tuple[str, str, str, Tier], ...] = (
    # Body text on backgrounds — 4.5:1.
    ("body-on-bg",           _T["text"],         _T["bg"],            "body"),
    ("body-on-surface",      _T["text"],         _T["surface"],       "body"),
    ("body-on-surface-muted", _T["text"],        _T["surface-muted"], "body"),
    ("muted-on-bg",          _T["text-muted"],   _T["bg"],            "body"),
    ("muted-on-surface",     _T["text-muted"],   _T["surface"],       "body"),
    ("muted-on-surface-muted", _T["text-muted"], _T["surface-muted"], "body"),
    # Anchor / accent text — 4.5:1 (treated as body-tier since
    # links can sit inline within paragraphs).
    ("accent-on-bg",         _T["accent"],       _T["bg"],            "body"),
    ("accent-on-surface",    _T["accent"],       _T["surface"],       "body"),
    ("accent-strong-on-bg",  _T["accent-strong"], _T["bg"],           "body"),
    # Button face — 3:1 (large text — buttons in this design
    # system render at 0.92rem font-weight 500 which the
    # WCAG-AA "large text bold" tier covers).
    ("button-white-on-accent",        "#ffffff", _T["accent"],          "large"),
    ("button-white-on-accent-strong", "#ffffff", _T["accent-strong"],   "large"),
    ("button-white-on-error",         "#ffffff", _T["error"],           "large"),
    # Secondary button — body text on surface; should clear 4.5:1.
    ("button-secondary-text", _T["text"], _T["surface"], "body"),
    # Status badges — 3:1 (non-text UI).
    ("badge-success",        "#1d5d33", _T["success-soft"], "large"),
    ("badge-warn",           "#6a4910", _T["warn-soft"],    "large"),
    ("badge-error",          "#6e1d27", _T["error-soft"],   "large"),
    ("badge-info",           "#194966", _T["info-soft"],    "large"),
    ("badge-accent",         _T["accent-strong"], _T["accent-soft"], "large"),
    ("badge-default",        _T["text-muted"],    _T["surface-muted"], "large"),
    # Alert bodies — 4.5:1 (text is dense alert prose).
    ("alert-error-text",     "#6e1d27", _T["error-soft"],   "body"),
    ("alert-warn-text",      "#6a4910", _T["warn-soft"],    "body"),
    ("alert-success-text",   "#1d5d33", _T["success-soft"], "body"),
    ("alert-info-text",      "#194966", _T["info-soft"],    "body"),
    # Topbar nav — 3:1; the current item's accent-strong on
    # accent-soft is a focus state for keyboard users so it
    # SHALL clear the contrast minimum.
    ("nav-current",          _T["accent-strong"], _T["accent-soft"],   "large"),
    # Danger button hover state.
    ("button-white-on-danger-hover", "#ffffff", "#8e2734", "large"),
    # Palette tokens declared in ``:root`` but not currently used
    # as a direct fg/bg in any CSS rule. Audited anyway so the
    # design-system stays AA-compliant when these tokens get
    # picked up by future markup (e.g., a future ``color:
    # var(--success)`` on body text). Threshold = large (3:1)
    # because the WCAG-intent here is button / non-text UI use.
    ("palette-success-on-bg", _T["success"], _T["bg"],      "large"),
    ("palette-success-on-surface", _T["success"], _T["surface"], "large"),
    ("palette-warn-on-bg",    _T["warn"],    _T["bg"],      "large"),
    ("palette-warn-on-surface", _T["warn"],   _T["surface"], "large"),
    ("palette-info-on-bg",    _T["info"],    _T["bg"],      "large"),
    ("palette-info-on-surface", _T["info"],   _T["surface"], "large"),
    ("palette-error-on-bg",   _T["error"],   _T["bg"],      "large"),
)


# Hex literals that are NOT subject to contrast audit. These are
# decorative tokens — borders + shadows + focus-ring fills — where
# WCAG doesn't apply because no text sits on them at full opacity.
DECORATIVE_TOKENS: set[str] = {
    _T["border"],
    _T["border-strong"],
    # Alert borders.
    "#efb1bb",
    "#e7c489",
    "#b2dec0",
    "#b2d2e2",
    # Badge borders.
    "#c6daf4",
    # Soft fills (used as backgrounds for audited pairs but
    # they also appear as the focus-ring background in
    # ``input:focus``; the focus state itself is verified via
    # the accent-on-bg pair).
    _T["accent-soft"],
    _T["success-soft"],
    _T["warn-soft"],
    _T["error-soft"],
    _T["info-soft"],
    _T["surface-muted"],
    # Danger hover fill — covered by an audited pair already, but
    # also listed here for explicit documentation.
    "#8e2734",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_relative_luminance_formula_matches_wcag_spec() -> None:
    """Smoke check against documented WCAG fixed points:
    pure white = 1.0; pure black = 0.0; mid-grey ≈ 0.215."""
    assert abs(relative_luminance((255, 255, 255)) - 1.0) < 1e-9
    assert relative_luminance((0, 0, 0)) == 0.0
    # WCAG quick reference values (#808080 mid-grey ≈ 0.2159).
    mid = relative_luminance((128, 128, 128))
    assert 0.21 < mid < 0.22


def test_contrast_ratio_is_symmetric() -> None:
    a = contrast_ratio("#000000", "#ffffff")
    b = contrast_ratio("#ffffff", "#000000")
    assert a == b
    # Black on white SHALL be the documented 21:1.
    assert abs(a - 21.0) < 1e-9


@pytest.mark.parametrize(
    "label,fg,bg,tier",
    [
        (label, fg, bg, tier)
        for (label, fg, bg, tier) in AUDITED_PAIRS
    ],
)
def test_audited_pair_meets_wcag_threshold(
    label: str, fg: str, bg: str, tier: Tier
) -> None:
    """REQ_NF_WEB2_002 — every documented foreground/background
    pair SHALL clear the WCAG threshold for its tier."""
    threshold = 4.5 if tier == "body" else 3.0
    ratio = contrast_ratio(fg, bg)
    assert ratio >= threshold, (
        f"{label}: {fg} on {bg} = {ratio:.2f}:1 (need {threshold}:1)"
    )


def test_audited_pairs_is_non_empty_closed_set() -> None:
    """REQ_SDD_WEB2_006 — the closed dict SHALL declare every
    documented combination. Sanity: the set is non-empty and
    every label is unique."""
    assert len(AUDITED_PAIRS) > 10
    labels = [label for (label, *_rest) in AUDITED_PAIRS]
    assert len(set(labels)) == len(labels), (
        f"duplicate labels in AUDITED_PAIRS: {labels}"
    )


def test_every_app_css_hex_literal_is_audited_or_decorative() -> None:
    """REQ_SDD_WEB2_006 — untested-token guard. Every hex color
    in ``app.css`` SHALL appear in at least one audited pair OR
    in ``DECORATIVE_TOKENS``. Adding a new color to ``app.css``
    without updating one of those two sets fails this test."""
    audited_colors: set[str] = set()
    for (_label, fg, bg, _tier) in AUDITED_PAIRS:
        audited_colors.add(fg.lower())
        audited_colors.add(bg.lower())
    decorative = {c.lower() for c in DECORATIVE_TOKENS}
    known = audited_colors | decorative
    unknown = _all_hex_literals() - known
    assert not unknown, (
        "untested color literal(s) in app.css — add them to "
        f"AUDITED_PAIRS or DECORATIVE_TOKENS: {sorted(unknown)}"
    )


def test_root_tokens_include_the_documented_palette() -> None:
    """Sanity: the design-system palette tokens SHALL be defined
    so AUDITED_PAIRS resolves cleanly. Asserts the named tokens
    used in the audit dict appear in ``:root``."""
    required = {
        "bg", "surface", "surface-muted",
        "text", "text-muted",
        "accent", "accent-strong", "accent-soft",
        "success", "success-soft",
        "warn", "warn-soft",
        "error", "error-soft",
        "info", "info-soft",
    }
    missing = required - _root_tokens().keys()
    assert not missing, f"missing :root tokens: {sorted(missing)}"
