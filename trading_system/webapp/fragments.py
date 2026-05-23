"""``fragment_context(request)`` — REQ_SDD_WEB2_001 + REQ_SDS_WEB2_002.

Every view route SHALL accept ``?fragment=<truthy>`` and, when
present, render only the inner content (no <html>/<head>/<body>
chrome) so HTMX-style swap targets receive a tight surface.

The helper returns a dict the view splats into the Jinja
context. Templates carry
``{% extends parent_template|default("base.html") %}`` so the
parent flips at render time without forking the template.
"""

from __future__ import annotations

from fastapi import Request


_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def is_fragment_request(request: Request) -> bool:
    """Return True iff the request asks for fragment-only render.

    Accepts ``?fragment=1`` / ``true`` / ``yes`` / ``on`` (case-
    insensitive). HTMX's ``HX-Request: true`` header also counts —
    even if the URL omits the query param, an HTMX-issued GET
    SHALL receive the fragment so the partial-swap pattern works
    transparently."""
    raw = request.query_params.get("fragment", "").strip().lower()
    if raw in _TRUTHY_VALUES:
        return True
    if request.headers.get("HX-Request") == "true":
        return True
    return False


def fragment_context(request: Request) -> dict:
    """Return Jinja-context kwargs that flip the parent template
    to the chrome-less fragment base when the request asks for
    fragment rendering. Splat into the existing
    ``TemplateResponse(..., context={...})``.
    """
    if is_fragment_request(request):
        return {"parent_template": "fragment_base.html"}
    return {"parent_template": "base.html"}
