"""aria-label discipline audit on status badges (REQ_SDD_WEB2_009).

REQ refs:
- REQ_NF_WEB2_005 — every status badge SHALL carry an aria-label.
- REQ_SDD_WEB2_009 — a test SHALL audit every ``class="badge ..."``
  in the webapp templates and reject any badge that doesn't carry
  ``aria-label``, ``aria-labelledby``, or a parent with
  ``aria-live`` / ``role="status"``.

The audit is structural: it greps every template under
``trading_system/webapp/templates/`` for the ``class="badge``
substring, isolates the enclosing tag, and asserts at least one
of the documented accessibility annotations is present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "trading_system" / "webapp" / "templates"


# Patterns that satisfy the audit when present in the badge tag
# OR (for the parent-aware annotations) in an immediately
# surrounding wrapper. The latter is matched by looking 4 lines
# upstream of the badge.
_LOCAL_ANNOTATIONS = (
    "aria-label=",
    "aria-labelledby=",
)
_AMBIENT_ANNOTATIONS = (
    'aria-live="',
    'role="status"',
)


def _template_files() -> list[Path]:
    return sorted(_TEMPLATES_DIR.rglob("*.html"))


_TAG_OPEN = re.compile(r"<[^>]*\bclass=[\"']([^\"']*)[\"'][^>]*>", re.DOTALL)


def _badge_occurrences(text: str) -> list[tuple[int, str]]:
    """Return ``(start_index, full_tag_string)`` for every element
    whose ``class`` attribute lists ``badge`` as a token.

    The opening-tag regex captures multi-line tags (Jinja attrs
    often wrap). The token check ensures we ignore
    ``class="my-badge"`` accidental substring hits.
    """
    out: list[tuple[int, str]] = []
    for match in _TAG_OPEN.finditer(text):
        classes = match.group(1).split()
        if "badge" not in classes:
            # Tolerate jinja-templated class lists like
            # ``class="badge {{ tone }}"`` — split removes the
            # `{{ tone }}` whole-word token but ``badge`` itself
            # is still in the list.
            joined = match.group(1)
            # The literal "badge" token MUST appear as a
            # whitespace-bounded word.
            if not re.search(r"\bbadge\b", joined):
                continue
        out.append((match.start(), match.group(0)))
    return out


def _is_inside_js_template_literal(text: str, idx: int) -> bool:
    """Heuristic: does ``idx`` sit inside a JS template literal?

    Template literals look like ``\\`<td><span class="badge ...
    ${t.side}</span></td>\\``` in the inline JS. We detect them
    by counting backticks before the position: an odd count
    means we're inside a literal."""
    # Strip multi-line comments + line comments to avoid
    # double-counting backticks that appear in comments. Cheap
    # heuristic — sufficient for the audit.
    prefix = text[:idx]
    # Drop /* ... */ comments.
    prefix = re.sub(r"/\*.*?\*/", "", prefix, flags=re.DOTALL)
    # Drop // line comments (preserves newlines so column counts
    # stay roughly the same).
    prefix = re.sub(r"//[^\n]*", "", prefix)
    return prefix.count("`") % 2 == 1


def _ambient_window(text: str, idx: int, *, lines: int = 4) -> str:
    """Return the window of text up to ``lines`` lines BEFORE ``idx``
    so the audit can spot a parent ``aria-live`` / ``role="status"``
    wrapper without doing a full DOM parse."""
    head = text[:idx]
    # Walk backwards and keep up to ``lines`` newlines.
    pieces = head.rsplit("\n", lines + 1)
    return "\n".join(pieces[-(lines + 1):])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_ANNOTATED_BADGES: list[tuple[str, str, str]] = []  # (template, tag, snippet)


for _path in _template_files():
    _text = _path.read_text(encoding="utf-8")
    for _start, _tag in _badge_occurrences(_text):
        # Skip badges that live INSIDE a JS template literal (the
        # dashboard's trades-table renderer). Those carry their
        # own aria-label in the literal but the audit can't grep
        # the tag boundary cleanly across backtick interpolation;
        # we cover those separately.
        if _is_inside_js_template_literal(_text, _start):
            _ANNOTATED_BADGES.append(
                (str(_path.relative_to(_REPO_ROOT)), _tag, _tag)
            )
            continue
        _snippet = _ambient_window(_text, _start) + _tag
        _ANNOTATED_BADGES.append(
            (str(_path.relative_to(_REPO_ROOT)), _tag, _snippet)
        )


def test_at_least_one_badge_exists_to_audit() -> None:
    """Sanity: the test would be vacuous if the templates had no
    badges. Pin the count so a future refactor that removes every
    badge fails this check instead of silently passing the audit."""
    assert len(_ANNOTATED_BADGES) >= 5


@pytest.mark.parametrize(
    "template,tag,snippet",
    _ANNOTATED_BADGES,
)
def test_every_badge_carries_aria_annotation(
    template: str, tag: str, snippet: str
) -> None:
    """REQ_NF_WEB2_005 + REQ_SDD_WEB2_009 — every badge SHALL carry
    aria-label (or aria-labelledby) on its own tag, OR sit inside
    a parent with aria-live / role="status"."""
    local_hit = any(marker in tag for marker in _LOCAL_ANNOTATIONS)
    ambient_hit = any(marker in snippet for marker in _AMBIENT_ANNOTATIONS)
    assert local_hit or ambient_hit, (
        f"badge in {template} carries no accessibility annotation:\n"
        f"  tag: {tag.strip()!r}\n"
        f"  Add aria-label=... to the badge OR wrap in a "
        f"role=\"status\"/aria-live=\"polite\" container."
    )


def test_js_rendered_badge_in_dashboard_carries_inline_aria_label() -> None:
    """The dashboard's trade-table renderer constructs badge
    HTML inside a JS template literal. The audit above skips
    JS-literal badges (backtick-bounded); pin them directly so a
    future template-literal edit can't accidentally drop the
    aria-label."""
    dashboard = (_TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert (
        'class="badge ${sideClass}" aria-label="Trade side ${t.side}"'
        in dashboard
    ), "JS-rendered trade badge lost its inline aria-label"
