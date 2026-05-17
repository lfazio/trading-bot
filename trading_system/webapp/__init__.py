"""FastAPI webapp for the CR-017 cascade.

Opt-in alternative to the stdlib ``webui/`` Phase-A surface. Install
with ``pip install trading-bot[webapp]`` and wire via
``trading_system.webapp.app.create_app(state)``.

REQ refs: REQ_F_FAS_001..007, REQ_NF_FAS_001..002, REQ_SDS_FAS_001..004,
REQ_SDD_FAS_001..007.
"""

from __future__ import annotations

from trading_system.webapp.app import WebappState, create_app

__all__ = ["WebappState", "create_app"]
