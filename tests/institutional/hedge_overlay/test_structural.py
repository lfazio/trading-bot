"""TC_HOV_010 — structural audits.

REQ refs:
- REQ_F_HOV_001 — module placement + import-graph closure.
- REQ_SDS_HOV_001 — L4 placement + ``InstrumentClass`` NOT extended
  with an ``OVERLAY`` value (the hedge-overlay subsystem keeps its
  own row types so consumers of ``models.instrument`` don't have to
  know overlay semantics).
- REQ_SDD_HOV_001 — closed import-graph.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trading_system.institutional.hedge_overlay as _package

_PACKAGE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "trading_system"
    / "institutional"
    / "hedge_overlay"
)
_INSTRUMENT_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "trading_system"
    / "models"
    / "instrument.py"
)

_ALLOWED_IMPORT_PREFIXES = (
    "trading_system.institutional.hedge_overlay",
    "trading_system.models",
    "trading_system.portfolio",
    "trading_system.tax",
    "trading_system.result",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "trading_system.risk",
    "trading_system.execution",
    "trading_system.safety",
    "trading_system.strategy_lab",
    "trading_system.accounts",
    "trading_system.notifications",
    "trading_system.webui",
    "trading_system.webapp",
    "trading_system.backtesting.monte_carlo",
)

_EXPECTED_PUBLIC_NAMES = frozenset(
    {
        "compute_portfolio_beta",
        "HedgeOverlay",
        "OverlayPolicy",
        "OverlayProposal",
        "IndexFuturePosition",
        "OverlayLedger",
        "OverlayError",
        "OverlayPositionState",
    }
)


def _python_files() -> list[Path]:
    return sorted(_PACKAGE_DIR.rglob("*.py"))


def test_import_graph_closed() -> None:
    """REQ_SDD_HOV_001 — every project-local import SHALL match an
    allow-listed prefix and SHALL NOT touch decisioning layers."""
    for py_file in _python_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if not module.startswith("trading_system."):
                    continue
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{py_file.name} imports {module} — "
                        f"REQ_SDD_HOV_001 forbids {forbidden}.*"
                    )
                assert any(
                    module.startswith(p) for p in _ALLOWED_IMPORT_PREFIXES
                ), (
                    f"{py_file.name} imports {module} — not in the "
                    "closed allow-list (REQ_SDD_HOV_001)"
                )


def test_instrument_class_has_no_overlay_value() -> None:
    """REQ_SDS_HOV_001 — the existing ``InstrumentClass`` enum SHALL
    NOT gain an ``OVERLAY`` value; the hedge-overlay subsystem keeps
    its own row types."""
    text = _INSTRUMENT_FILE.read_text(encoding="utf-8")
    assert "OVERLAY" not in text, (
        "InstrumentClass SHALL NOT carry an OVERLAY value — REQ_SDS_HOV_001 "
        "keeps hedge-overlay rows out of the broader InstrumentClass enum"
    )


def test_public_surface_re_exports_documented_names() -> None:
    """The package ``__init__.py`` SHALL re-export the public surface
    so consumers can ``from trading_system.institutional.hedge_overlay
    import ...`` without reaching into sub-modules."""
    for name in _EXPECTED_PUBLIC_NAMES:
        assert hasattr(_package, name), (
            f"trading_system.institutional.hedge_overlay missing "
            f"public name {name!r}"
        )
    public_all = frozenset(getattr(_package, "__all__", ()))
    # Every documented name SHALL appear in __all__ (the public-surface
    # contract for downstream tooling).
    assert _EXPECTED_PUBLIC_NAMES.issubset(public_all), (
        f"__all__ missing: {_EXPECTED_PUBLIC_NAMES - public_all}"
    )
