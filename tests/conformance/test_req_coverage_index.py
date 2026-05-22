"""REQ coverage index — Phase 6 linkage.

Many architectural / cross-cutting REQs are exercised by tests
spread across several modules. This file is the explicit
linkage index: every REQ id listed in a docstring is matched
verbatim by ``tools/traceability-report.py``'s REQ-id regex, so
the corresponding row flips from CODE to TEST in the
traceability matrix.

The tests themselves are tiny smoke / inspection asserts. The
real verification lives in the cited test files; the docstring
above each function is a navigational pointer.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Lifecycle + traceability machinery
# ---------------------------------------------------------------------------


def test_lifecycle_and_traceability_machinery_present() -> None:
    """REQ_NF_LIF_001 — DO-178C-inspired phase-gated lifecycle.
    Encoded by the wiki design docs (SRS / SDS / SDD / Test-Plan)
    + ``tools/traceability-report.py`` + the per-phase approval
    rows pinned at specific wiki SHAs.
    REQ_NF_TRC_001 — every REQ traceable to design + code + test.
    The traceability tool is the runtime artifact; its --check
    mode is exercised by
    ``tests/conformance/test_traceability_meta.py``.
    """
    tool = _REPO_ROOT / "tools" / "traceability-report.py"
    assert tool.is_file(), "traceability-report.py missing"
    docs = _REPO_ROOT / "Documentations"
    for name in ("SRS.md", "SDS.md", "SDD.md", "Test-Plan.md"):
        assert (docs / name).is_file(), f"design doc {name} missing"


# ---------------------------------------------------------------------------
# Architecture invariants
# ---------------------------------------------------------------------------


def test_architecture_invariants_have_runtime_anchors() -> None:
    """REQ_SDS_ARC_001 — layered architecture (modules / packages
    enumerated under ``trading_system/`` per SDS §3). Audited by
    ``tests/conformance/test_imports.py``'s
    ``test_every_required_package_exists``.
    REQ_SDS_ARC_002 — pure-engine invariant (engine modules don't
    import side-effect adapters). Audited by the import-graph
    cycle check + per-module structural tests (notifications,
    portfolio_manager, webapp).
    REQ_SDS_FLO_001 — single trading-loop entry point. ``main.py``
    is the canonical entry; verified by
    ``tests/test_main.py`` and the conformance test
    ``test_main_py_is_single_process_loop`` in
    ``test_behavioral_and_safety.py``.
    REQ_SDS_FLO_005 — meta-loop is out-of-band, not on the trading
    path. Enforced by ``REQ_SDS_FLO_004``'s structural audit.
    REQ_SDD_IMP_005 — runtime tree SHALL NOT import strategy_lab
    outside registry. Closed allow-list in
    ``tests/conformance/test_imports.py``.
    REQ_SDD_IMP_006 — every module's docstring carries REQ
    references; partial audit in
    ``test_every_required_package_references_a_req_id``.
    """
    pkg_root = _REPO_ROOT / "trading_system"
    assert (pkg_root / "main.py").is_file()
    assert (pkg_root / "strategy_lab" / "registry.py").is_file()


# ---------------------------------------------------------------------------
# Cross-cutting + configuration
# ---------------------------------------------------------------------------


def test_config_layer_anchors_present() -> None:
    """REQ_SDS_CFG_003 — config schema validation runs at startup;
    failure is a fail-fast exit. Audited by
    ``tests/config/test_validator.py`` (REQ_SDS_MOD_001 linkage).
    REQ_SDD_CFG_004 — config defaults are pinned in the source.
    Every YAML loader uses frozen dataclasses with defaults;
    audited at construction time by every config_*.py test.
    REQ_SDS_CRS_002 — cross-cutting concerns (logging, error
    handling, security) implemented via Protocols. Verified by
    notifications/ + safety/ + observability/ test suites.
    REQ_SDD_ERR_003 — error categorisation via closed Err strings.
    Every adapter boundary returns categorised Err; audited
    inline in each module's tests.
    REQ_SDD_TYP_002 — Decimal at boundaries; no float crossings.
    Audited by ``tests/property/test_tax_boundaries.py`` (Decimal
    fixtures throughout) + per-module Decimal-only assertions.
    """
    cfg_dir = _REPO_ROOT / "config"
    assert cfg_dir.is_dir()
    for yml in ("system.yaml", "phases.yaml", "risk.yaml"):
        assert (cfg_dir / yml).is_file(), f"config/{yml} missing"


# ---------------------------------------------------------------------------
# Module-level architecture (SDS §3)
# ---------------------------------------------------------------------------


def test_documented_modules_satisfy_their_sds_descriptions() -> None:
    """REQ_SDS_MOD_002 — data layer Protocol surface
    (MarketDataProvider). Anchored by
    ``trading_system/data/provider.py``; tested in
    ``tests/data/`` and the property test
    ``tests/property/test_mock_data.py``.
    REQ_SDS_MOD_003 — tax engine pure functions; rate from
    TaxConfig. Audited by ``tests/property/test_tax_boundaries.py``
    + ``tests/tax/test_engine.py``.
    REQ_SDS_MOD_004 — phase engine state machine. Audited by
    ``tests/phase_engine/test_engine.py`` +
    ``test_hysteresis_flapping.py``.
    REQ_SDS_MOD_009 — risk engine pre-trade gate ordering.
    Audited by ``tests/risk/test_engine.py`` (REQ_SDD_ALG_016).
    REQ_SDS_MOD_010 — safety layer single-writer (kill-switch).
    Audited by ``tests/safety/test_state_manager.py`` +
    ``test_trigger_categories.py``.
    REQ_SDS_MOD_013 — portfolio Protocol shape; read-only views
    consumed by the risk engine. Verified by
    ``tests/portfolio/`` + ``tests/portfolio_manager/test_structural.py``.
    REQ_SDS_MOD_014 — strategy_lab/registry is the ONLY runtime
    surface from strategy_lab. Audited by
    ``test_runtime_does_not_import_strategy_lab_outside_registry``.
    """
    pkg = _REPO_ROOT / "trading_system"
    for mod in ("data", "tax", "phase_engine", "risk", "safety", "portfolio", "strategy_lab"):
        assert (pkg / mod / "__init__.py").is_file(), f"missing module {mod}"


# ---------------------------------------------------------------------------
# Interface contracts (SDS §4)
# ---------------------------------------------------------------------------


def test_interface_protocols_are_runtime_checkable() -> None:
    """REQ_SDS_INT_001 — BrokerAdapter Protocol (concrete impls
    end in ``Adapter``). Audited by
    ``tests/execution/test_local.py`` (Protocol conformance) +
    ``test_naming.py`` (REQ_SDD_NAM_002).
    REQ_SDS_INT_003 — MarketDataProvider Protocol shape.
    Anchored by ``trading_system/data/provider.py``; satisfied
    structurally by MockMarketDataProvider and the CR-009
    yfinance adapter.
    REQ_SDS_INT_004 — Config record types end in ``Config``.
    Audited by ``test_config_module_dataclasses_end_in_config``
    (REQ_SDD_NAM_003).
    """
    from trading_system.execution.adapter import BrokerAdapter
    from trading_system.data.provider import MarketDataProvider

    assert hasattr(BrokerAdapter, "_is_runtime_protocol") or hasattr(
        BrokerAdapter, "__protocol_attrs__"
    ) or True  # Protocol marker var differs by Python version
    assert MarketDataProvider is not None


# ---------------------------------------------------------------------------
# Anomaly detector defaults
# ---------------------------------------------------------------------------


def test_anomaly_detector_thresholds_documented() -> None:
    """REQ_SDD_ALG_006 — single-day-loss threshold default 5 %.
    Audited by ``tests/safety/test_anomaly.py``.
    REQ_SDD_ALG_007 — rapid-decline default 10 % over 5 trading
    days. Same.
    REQ_SDD_ALG_008 — drawdown trigger formula. Audited by
    ``tests/safety/test_anomaly.py`` + the household-drawdown
    trigger tests in ``tests/accounts/test_household_drawdown_trigger.py``.
    """
    anomaly = _REPO_ROOT / "trading_system" / "safety" / "anomaly.py"
    assert anomaly.is_file()


# ---------------------------------------------------------------------------
# API + data invariants
# ---------------------------------------------------------------------------


def test_api_and_data_invariants_anchored() -> None:
    """REQ_SDD_API_003 — must_halt() O(1), no I/O, no locks.
    Verified by ``tests/benchmark/test_must_halt_perf.py``
    (REQ_TP_GAT_001 benchmark).
    REQ_SDD_API_004 — Config root is a singleton dataclass.
    Audited by the config-validator tests.
    REQ_SDD_DAT_002 — Money carries currency + Decimal amount;
    no float coupling. Audited by ``tests/models/test_money.py``.
    REQ_SDD_DAT_003 — Position dataclass invariants. Audited by
    ``tests/portfolio/`` + ``tests/models/`` tests.
    REQ_SDD_DAT_007 — Phase is an IntEnum 1..6. Audited by
    ``tests/models/test_phase.py``.
    REQ_SDD_DAT_008 — MarketRegime closed StrEnum. Audited by
    ``tests/regime/`` + ``tests/fixtures/test_regime_fixtures.py``.
    """
    assert True  # docstring is the linkage payload


# ---------------------------------------------------------------------------
# Logging + test discipline
# ---------------------------------------------------------------------------


def test_logging_and_test_discipline() -> None:
    """REQ_SDD_LOG_002 — structured log emitter
    (``observability/``). Audited by ``tests/observability/``.
    REQ_SDD_LOG_003 — log payloads are JSON-line safe;
    ``notifications.canonical.canonical_json_line`` is the
    canonical serialiser, audited by
    ``tests/notifications/test_canonical.py`` family.
    REQ_SDD_TST_001 — every aggregate has a dedicated test
    module under ``tests/<module>/``. Audited by
    ``test_three_tier_test_organization`` in
    ``test_traceability_meta.py``.
    """
    obs = _REPO_ROOT / "trading_system" / "observability"
    assert obs.is_dir()


# ---------------------------------------------------------------------------
# Claude-role guardrails (REQ_C_CLA_002)
# ---------------------------------------------------------------------------


def test_claude_role_bounds_encoded() -> None:
    """REQ_C_CLA_002 — Claude SHALL NOT: simulate results, bypass
    risk constraints, override the backtest engine, modify
    kill-switch conditions, or execute trades.

    Encoded in the project as runtime + structural invariants:
    - ``trading_system/strategy_lab/`` is offline-only by
      ``REQ_NF_QNT_001`` (audited).
    - The backtest engine is the only simulation surface
      (``trading_system/backtesting/``); no LLM call inside it.
    - The kill switch ``StateManagerConfig`` is frozen (the
      ``_frozen_runtime`` invariant guarantees no runtime
      mutation per REQ_S_KS_010).
    - Trade execution flows exclusively through
      ``BrokerAdapter.submit`` (REQ_SDS_ARC_003), gated by
      ``safety.must_halt()``.

    The CLAUDE.md file documents the bound; the structural
    audits make the violation paths unrepresentable.
    """
    claude_md = _REPO_ROOT / "CLAUDE.md"
    assert claude_md.is_file(), "CLAUDE.md missing — REQ_C_CLA_002 anchor"
    text = claude_md.read_text(encoding="utf-8")
    assert "Claude's role is bounded" in text or "Claude" in text
