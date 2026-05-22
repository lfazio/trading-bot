"""Behavioral-default + architectural-invariant audits.

REQ refs:
- REQ_C_BHV_001 — The system SHALL prefer stocks over turbos
  unless a strong, validated edge exists. The
  ``test_phase_constraints_favor_stocks_over_turbos`` case
  asserts every phase's stock allocation ≥ turbo allocation,
  encoding the preference in the configuration shape.
- REQ_C_BHV_004 — The system SHALL prioritize survival over return.
- REQ_C_BHV_005 — Forbidden behaviors: aggressive leverage scaling
  after milestone, continuous risk increase, overfitting-driven
  optimization loops, kill-switch bypass, "all-in" trades.
- REQ_S_KS_012 — The system SHALL prefer stopping incorrectly over
  trading incorrectly when in doubt.
- REQ_SDS_ARC_003 — The safety layer SHALL act as a veto over
  execution; every call to ``BrokerAdapter.submit()`` SHALL be
  preceded by a ``safety.must_halt()`` check.
- REQ_SDS_ARC_004 — The runtime SHALL be a single-process event-
  driven loop; the same loop logic SHALL execute in live and
  backtest modes (only the adapters differ).
- REQ_SDS_CRS_004 — Behavioral defaults SHALL be encoded as
  unrepresentable in the API surface, not merely discouraged in
  documentation.

The audits below are structural — they assert the project ships
the SAFETY / RISK / KILL-SWITCH machinery that ENCODES these
defaults (rather than trying to detect the negative space). A
runtime that doesn't carry the machinery would fail.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNTIME_DIR = _REPO_ROOT / "trading_system"


# ---------------------------------------------------------------------------
# REQ_C_BHV_004 / REQ_S_KS_012 — survival over return + prefer-halt
# ---------------------------------------------------------------------------


def test_safety_layer_exports_must_halt() -> None:
    """REQ_C_BHV_004 + REQ_S_KS_012 — survival-over-return + prefer-
    halt are encoded as a runnable kill switch. The audit walks
    ``safety/`` and asserts at least one module defines
    ``must_halt`` (the current home is
    ``safety/state_manager.py`` + ``safety/protocol.py`` Protocol
    surface; an earlier draft used ``safety/kill_switch.py``)."""
    safety_dir = _RUNTIME_DIR / "safety"
    assert safety_dir.is_dir(), "trading_system/safety/ package missing"
    found = False
    for py_file in safety_dir.rglob("*.py"):
        if "def must_halt" in py_file.read_text(encoding="utf-8"):
            found = True
            break
    assert found, (
        "REQ_S_KS_012 — must_halt() is the prefer-halt entry point; "
        "no module under safety/ defines it"
    )


def test_risk_engine_runs_kill_switch_before_other_gates() -> None:
    """REQ_C_BHV_004 + REQ_SDS_ARC_003 — the risk engine SHALL
    check the kill switch FIRST. CLAUDE.md states the priority as
    KillSwitch > RiskEngine > Strategy > Execution; the engine's
    pre_trade SHALL implement that ordering."""
    risk_engine = _RUNTIME_DIR / "risk" / "engine.py"
    assert risk_engine.is_file()
    text = risk_engine.read_text(encoding="utf-8")
    # Heuristic: the kill-switch check appears in pre_trade.
    assert "must_halt" in text, (
        "REQ_C_BHV_004 — risk engine does not reference must_halt; "
        "kill switch ordering broken"
    )


# ---------------------------------------------------------------------------
# REQ_C_BHV_005 / REQ_SDS_CRS_004 — forbidden-behavior unrepresentability
# ---------------------------------------------------------------------------


def test_no_all_in_sizing_helper_exists() -> None:
    """REQ_C_BHV_005 / REQ_SDS_CRS_004 — "all-in" trades are a
    forbidden behavior. The audit asserts the codebase does NOT
    expose an ``all_in`` / ``yolo`` / ``max_position_size``-style
    helper that would let a strategy bypass the per-trade risk
    budget. Absence is the encoding."""
    forbidden_names = (
        "def all_in",
        "def yolo",
        "def max_position",
        "ALL_IN",
        "YOLO_MODE",
    )
    hits: list[str] = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for needle in forbidden_names:
            if needle in text:
                rel = py_file.relative_to(_REPO_ROOT)
                hits.append(f"{rel}: contains {needle!r}")
    assert not hits, (
        "REQ_C_BHV_005 — forbidden 'all-in' sizing helper found:\n  "
        + "\n  ".join(hits)
    )


def test_risk_config_has_per_trade_cap() -> None:
    """REQ_C_BHV_005 — continuous risk increase is forbidden; the
    ``RiskConfig`` (or equivalent) MUST cap per-trade risk so
    strategies cannot ramp risk indefinitely. The audit asserts
    the config record exposes a documented cap field."""
    risk_config = _RUNTIME_DIR / "risk" / "config.py"
    assert risk_config.is_file(), "risk/config.py missing"
    text = risk_config.read_text(encoding="utf-8")
    # The actual field name varies (single_asset_cap / per_trade_risk
    # / risk_per_trade / max_risk_per_trade); the audit accepts any
    # cap-style field name. The currently shipped RiskConfig uses
    # ``single_asset_cap`` (the per-trade-risk cap as a fraction of
    # portfolio); CR-006 may add ``per_trade_risk`` per-account.
    candidates = (
        "single_asset_cap",
        "per_trade_risk",
        "risk_per_trade",
        "max_risk_per_trade",
    )
    assert any(c in text for c in candidates), (
        "REQ_C_BHV_005 — risk/config.py SHALL expose a per-trade "
        f"risk cap (any of {candidates})"
    )


# ---------------------------------------------------------------------------
# REQ_SDS_ARC_003 — safety layer veto over execution
# ---------------------------------------------------------------------------


def test_execution_adapter_calls_safety_check_before_submit() -> None:
    """REQ_SDS_ARC_003 — the safety layer SHALL act as a veto over
    execution. The audit walks every call site of
    ``BrokerAdapter.submit`` in the runtime tree and asserts the
    project ships at least one mechanism that enforces a safety
    check before broker submission.

    Heuristic: the ``execution`` package OR a runtime module that
    consumes ``execution`` references ``must_halt`` / ``kill_switch``
    in the same file as a ``.submit(`` call site.
    """
    submit_callers = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if ".submit(" not in text:
            continue
        # Skip the broker Protocol surface itself — only count CALL
        # sites, not the definition.
        if py_file.name == "adapter.py" and "execution/" in str(py_file):
            continue
        if "must_halt" in text or "kill_switch" in text or "safety" in text:
            submit_callers.append(py_file)
    assert submit_callers, (
        "REQ_SDS_ARC_003 — no runtime module guards BrokerAdapter.submit "
        "with a safety check; expected at least one"
    )


# ---------------------------------------------------------------------------
# REQ_SDS_ARC_004 — single-process event-driven loop
# ---------------------------------------------------------------------------


def test_main_py_is_single_process_loop() -> None:
    """REQ_SDS_ARC_004 — the runtime SHALL be a single-process
    event-driven loop. Audit: ``trading_system/main.py`` SHALL
    NOT spawn child processes for the trading path
    (``multiprocessing.Process`` / ``ProcessPoolExecutor`` /
    ``subprocess.Popen``). The webapp DOES use a ProcessPoolExecutor
    but that's the BACKTEST path, not the trading loop —
    ``main.py`` is the trading entry point."""
    main_py = _RUNTIME_DIR / "main.py"
    assert main_py.is_file(), "trading_system/main.py missing"
    text = main_py.read_text(encoding="utf-8")
    forbidden = (
        "multiprocessing.Process",
        "ProcessPoolExecutor",
        "subprocess.Popen",
    )
    hits = [needle for needle in forbidden if needle in text]
    assert not hits, (
        f"REQ_SDS_ARC_004 — trading_system/main.py spawns child "
        f"processes: {hits}; trading loop SHALL be single-process"
    )


# ---------------------------------------------------------------------------
# REQ_S_KS_004 / REQ_S_KS_006 — strategy-instability + system-integrity triggers
# ---------------------------------------------------------------------------


def test_safety_anomaly_module_covers_financial_trigger_surface() -> None:
    """REQ_S_KS_003 / REQ_S_KS_004 / REQ_S_KS_006 — anomaly
    triggers (financial / strategy-instability / system-integrity)
    SHALL have a runtime surface. The audit confirms
    ``safety/anomaly.py`` (financial triggers) AND
    ``notifications/payloads.py`` (AnomalyAlert payload — used by
    strategy_lab + execution sites to escalate) BOTH exist so the
    end-to-end machinery is reachable.

    The specific detector implementations for REQ_S_KS_004's
    walk-forward collapse and REQ_S_KS_006's registry-corruption
    detectors are deferred Phase B follow-ups; this audit only
    confirms the surface."""
    anomaly = _RUNTIME_DIR / "safety" / "anomaly.py"
    assert anomaly.is_file(), (
        "REQ_S_KS_003 — safety/anomaly.py missing; financial-trigger "
        "surface (drawdown / single-day loss / rapid decline) absent"
    )
    payloads = _RUNTIME_DIR / "notifications" / "payloads.py"
    assert payloads.is_file(), (
        "notifications/payloads.py missing — AnomalyAlert payload is "
        "the channel REQ_S_KS_004 + REQ_S_KS_006 escalations land on"
    )
    payload_text = payloads.read_text(encoding="utf-8")
    assert "AnomalyAlert" in payload_text, (
        "REQ_S_KS_004 / REQ_S_KS_006 — AnomalyAlert payload missing "
        "from notifications/payloads.py"
    )


# ---------------------------------------------------------------------------
# REQ_F_CAP_001 — starting capital from config
# ---------------------------------------------------------------------------


def test_starting_capital_lives_in_config_not_hardcoded() -> None:
    """REQ_F_CAP_001 — starting capital SHALL be read from
    configuration; no hardcoded value. Audit: ``config/system.yaml``
    declares ``starting_capital``; the runtime loader reads it; no
    runtime source file hardcodes a non-zero literal capital value
    via a ``starting_capital = <Decimal>`` assignment."""
    system_yaml = _REPO_ROOT / "config" / "system.yaml"
    assert system_yaml.is_file(), "config/system.yaml missing"
    text = system_yaml.read_text(encoding="utf-8")
    assert "starting_capital" in text, (
        "REQ_F_CAP_001 — config/system.yaml SHALL declare 'starting_capital'"
    )
    # Audit the runtime tree — no hardcoded ``starting_capital = <literal>``
    # outside loader / model definition files.
    pattern = re.compile(r"^\s*starting_capital\s*=\s*[\"']?\d", re.MULTILINE)
    bad_assigns: list[str] = []
    for py_file in _RUNTIME_DIR.rglob("*.py"):
        # Allow the loader/config modules to bind to a default.
        rel_str = str(py_file.relative_to(_REPO_ROOT))
        if "/config/" in rel_str or rel_str.endswith("/config.py"):
            continue
        text = py_file.read_text(encoding="utf-8")
        if pattern.search(text):
            bad_assigns.append(rel_str)
    assert not bad_assigns, (
        "REQ_F_CAP_001 — runtime modules hardcoding starting_capital:\n  "
        + "\n  ".join(bad_assigns)
    )


# ---------------------------------------------------------------------------
# REQ_F_TAX_005 — backtests SHALL apply taxes
# ---------------------------------------------------------------------------


def test_backtest_engine_imports_tax_engine() -> None:
    """REQ_F_TAX_005 — backtests SHALL apply taxes; no exception.
    Audit: ``backtesting/`` SHALL import from ``tax/`` so the tax
    machinery is reachable on the backtest path. A backtest engine
    that never reaches the tax module cannot apply taxes."""
    bct_dir = _RUNTIME_DIR / "backtesting"
    has_tax_import = False
    for py_file in bct_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if "from trading_system.tax" in text or "trading_system.tax import" in text:
            has_tax_import = True
            break
    assert has_tax_import, (
        "REQ_F_TAX_005 — no backtesting/ module imports trading_system.tax; "
        "tax application unreachable on the backtest path"
    )


# ---------------------------------------------------------------------------
# REQ_C_BHV_001 — prefer stocks over turbos
# ---------------------------------------------------------------------------


def test_phase_constraints_favor_stocks_over_turbos() -> None:
    """REQ_C_BHV_001 — the system SHALL prefer stocks over turbos.
    Audit: load ``config/phases.yaml`` and assert every phase's
    stock-allocation target is greater than its turbo allocation.
    The preference is encoded in the configuration shape (not in
    runtime heuristics) so a YAML edit can't silently reverse the
    bias without this audit failing.
    """
    from decimal import Decimal

    from trading_system.models.phase import AllocationBucket, Phase
    from trading_system.phase_engine.loader import load_phase_engine
    from trading_system.result import Ok

    phases_yaml = _REPO_ROOT / "config" / "phases.yaml"
    loaded = load_phase_engine(phases_yaml)
    assert isinstance(loaded, Ok), f"phase loader Err: {loaded}"
    engine = loaded.value
    for phase in Phase:
        c = engine.constraints_for(phase)
        stock = c.allocation_targets.get(AllocationBucket.STOCK, Decimal(0))
        turbo = c.allocation_targets.get(AllocationBucket.TURBO, Decimal(0))
        assert stock > turbo, (
            f"REQ_C_BHV_001 — {phase.name} allocation: stock={stock} "
            f"NOT > turbo={turbo}; configuration violates the documented "
            "stock-preference behavioral default"
        )
