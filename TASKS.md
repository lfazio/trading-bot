# Trading Bot — Task Breakdown

Source of truth: [`trading-bot.md`](./trading-bot.md). All tasks below trace back to that
spec. The lifecycle is DO-178C-inspired and **gated** — no task in a later phase may start
until the gate of the previous phase is reviewed and approved.

Legend: `[ ]` open · `[~]` in progress · `[x]` done · `(REQ_xxx)` traceability id

---

## Phase 0 — Repository Bootstrap

- [x] Decide Python version (3.11+) and pin in `pyproject.toml`
- [x] Set up project skeleton (`pyproject.toml`, `ruff`, `mypy`, `pytest`, `pre-commit`)
- [x] Add `Makefile` (or `just`) with: `lint`, `typecheck`, `test`, `backtest`, `run`
- [x] Add `.env.example` for broker credentials (never commit secrets); document one
      block per supported adapter
- [x] Add `config/` defaults: `starting_capital`, `currency`, `phase_thresholds`,
      `broker.adapter` (selects which `BrokerAdapter` implementation to use)
- [x] Initialize traceability matrix file (`docs/traceability.csv` — REQ ↔ SDS ↔ SDD ↔ code ↔ test)
- [x] Implement `trading_system/result.py` — Rust-style `Option[T]` (`Some` |
      `Nothing`) and `Result[T, E]` (`Ok` | `Err`) tagged unions, frozen
      dataclasses, with `is_ok` / `is_err` / `map` / `and_then` /
      `unwrap_or` / `unwrap_or_else` / `unwrap`. Stdlib only. No exception
      handling for control flow anywhere downstream.

**Gate:** repo skeleton reviewed; no business logic yet.

---

## Phase 1 — SRS (Software Requirements Specification) ✅ APPROVED 2026-04-29 @ 7424909

Output: `Documentations/SRS.md` (in wiki submodule). **No design or code allowed.**

- [x] Functional requirements (REQ_F_xxx)
  - [x] Capital lifecycle: configurable starting capital; six-phase scaling
  - [x] After-tax optimization (France CTO, 30% flat)
  - [x] Tax-aware trade gate: `expected_net_profit > 5 × total_fees AFTER TAX`
  - [x] EU dividend + swing stock investing
  - [x] Tactical trading (weeks–months)
  - [x] Turbo / CFD trading with strict, phase-capped selection
  - [x] Generic broker adapter interface with `LocalBrokerAdapter` as the in-process reference implementation; live-broker adapters deferred until a broker is selected
  - [x] Capital flow tracking (initial + injections, performance net of inflows)
  - [x] Structured products overlay (max 10%, regime-gated)
  - [x] Meta-optimization loop (bounded strategy research, not autonomous trading)
  - [x] Global kill switch (3 states: ACTIVE / DEGRADED / KILL)
  - [x] Milestone controller (2k / 5k / 10k / 20k / 50k / 100k / 200k / 500k / 1M / 2M / 5M €; thresholds configurable)
  - [x] Phase 5 capabilities: tax-loss harvesting, sector rotation, currency hedging
  - [x] Phase 6 capabilities: vol-targeting, risk parity, multi-strategy ensemble, hedge overlay, NAV/attribution reporting
- [x] Non-functional requirements (REQ_NF_xxx)
  - [x] Determinism in backtest engine
  - [x] Reproducibility of strategy versions
  - [x] Auditability (full state snapshot on kill switch)
- [x] Constraints (REQ_C_xxx)
  - [x] Tax: 30% on realized gains and dividends
  - [x] Risk per trade: phase 1–3 = 1–2%, phase 4 = 1–1.5%, phase 5 = 0.5–1%, phase 6 = 0.25–0.75%; stop-loss mandatory
  - [x] Max drawdown: phases 1–2 = 15%, phases 3–4 = 20%, phase 5 = 15%, phase 6 = 12%
  - [x] Portfolio-level vol cap mandatory in phases 5–6 (in addition to per-trade limits)
  - [x] Position limits: stocks 25–35%, turbos phase-capped (≤ 5 / 15 / 20 / 15 / 10 % across phases 2–6)
  - [x] Starting capital and phase thresholds are read from `config/`, never hardcoded
- [x] Assign REQ id to every requirement; populate traceability matrix col 1

**Gate:** SRS explicitly reviewed and approved before SDS.

---

## Phase 2 — SDS (System Design Specification) ✅ APPROVED 2026-04-29 @ 26ce913

Output: `Documentations/SDS.md` (in wiki submodule). **No low-level code allowed.**

- [x] High-level architecture diagram (modules + data flows)
- [x] Module decomposition (must match spec layout)
  - `config/`, `data/`, `models/`, `screener/`, `strategies/`, `risk/`, `tax/`,
    `backtesting/`, `portfolio/`, `execution/`, `phase_engine/`, `turbo_selector/`,
    `dashboard/`, `safety/`, `strategy_lab/`, `milestone_controller/`, `analytics/`
- [x] External interfaces
  - [x] Generic `BrokerAdapter` interface (orders, positions, leveraged instruments, account state)
  - [x] `LocalBrokerAdapter` reference adapter contract — in-process, deterministic, simulates fills / fees / slippage; the only concrete adapter shipped through this lifecycle
  - [x] Market data source(s); pluggable behind a `MarketDataProvider` interface
- [x] Phase engine design (auto-detect by `equity + injected_capital`; thresholds from config; six phases)
- [x] Kill switch override hierarchy: `KillSwitch > RiskEngine > Strategy > Execution`
- [x] Strategy lab orchestration (generator → backtester → evaluator → risk_guard → optimizer → registry → loop_controller)

**Gate:** every SRS REQ id maps to an SDS component; traceability matrix col 2 filled.

---

## Phase 3 — SDD (Software Design Description) ✅ APPROVED 2026-04-29 @ 9ee11d5

Output: `Documentations/SDD.md` (in wiki submodule). Pseudo-code only; map 1:1 to SDS.

- [x] Class/data-structure design per module
- [x] Algorithms
  - [x] Tax module: `net_profit = gross * 0.70`; `net_dividend = dividend * 0.70`
  - [x] Tax-aware trade gate function
  - [x] Turbo selection: filter → score → select (with weights 0.35/0.25/0.20/0.20)
  - [x] Risk engine constraints (drawdown, position, per-trade)
  - [x] Phase engine state machine (phases 1–6, configurable thresholds, hysteresis on downgrades)
  - [x] Phase 5 modules: tax-loss harvester, sector rotator, currency hedger
  - [x] Phase 6 modules: vol-target sizer, risk-parity allocator, strategy ensemble, hedge-overlay manager, NAV/attribution reporter
  - [x] Capital flow accounting (excludes injections from performance metrics)
  - [x] Meta-loop scoring: `0.4*ret + 0.3*sharpe + 0.2*stability + 0.1*dd_penalty`
  - [x] Walk-forward validator
  - [x] Kill switch trigger evaluator + recovery checker
  - [x] Milestone controller (gradual +10–20% exposure unlock)
  - [x] Structured product decomposer (reject if not decomposable)
  - [x] Regime detector (bull / bear / sideways / high-vol)

**Gate:** traceability matrix col 3 (SDD) complete; reviewed against SDS.

---

## Phase 4 — Test Plan Design ✅ APPROVED 2026-04-30 @ wiki 2127a35

Output: `Documentations/Test-Plan.md` (in wiki submodule). **No implementation allowed.**

- [x] Unit test plan per module
- [x] Integration test plan (screener → strategy → risk → execution)
- [x] Backtest validation plan (deterministic; tax-inclusive; injection timeline)
- [x] Risk validation tests (drawdown caps, position caps, turbo cap)
- [x] Tax correctness tests (gross→net; dividend; trade-gate boundary)
- [x] Edge cases: market crash, turbo knockout, broker rejection, data outage
- [x] Walk-forward / out-of-sample tests for every strategy candidate
- [x] Kill switch tests (all trigger categories + recovery + manual override)
- [x] Structured product stress tests (crash / vol expansion / correlation spike)

**Gate:** every REQ has ≥ 1 test case; traceability matrix col 4 complete.

---

## Phase 5 — Implementation

Follow the spec's mandatory order. Each module must reference its REQ ids in module
docstring.

1. [x] `models/` — domain entities (Position, Trade, Order, Instrument, Turbo, ...) ✅ DONE 2026-05-01 @ b12043c
2. [x] `data/` — market data layer (cache, feeds, validation) ✅ DONE 2026-05-01 @ f385314
3. [x] `tax/` — France CTO tax engine + tax-aware trade gate ✅ DONE 2026-05-01 @ e823e4a
4. [x] `execution/` — generic `BrokerAdapter` interface + `LocalBrokerAdapter` (in-process deterministic broker simulating orders, fills, positions, turbos/CFDs, fees, slippage); live-broker adapters deferred ✅ DONE 2026-05-02 @ c47273f (MARKET orders only; LIMIT/STOP deferred to follow-up)
5. [x] `phase_engine/` — phase detection + constraint enforcement ✅ DONE 2026-05-02 (added `AllocationBucket` StrEnum + `REQ_SDD_TYP_004`; SDD/Test-Plan re-approved at wiki d724b2f)
6. [x] `screener/` — EU dividend/stock screener (yield 3–7%, payout <70%, FCF>0, D/E<1.5, ≥5y history) ✅ DONE 2026-05-02 (filter + scored ranking, score helpers documented as pragmatic stand-ins for the SDD's hypothetical inputs since `Fundamentals` does not yet carry dividend-growth std-dev or P/FCF multiple — choices captured in the engine docstring; no design re-approval needed)
7. [x] `strategies/` — core (long-term/dividend) + tactical (trend, breakout, pullback) ✅ DONE 2026-05-03 (Strategy + PortfolioView Protocols, MarketState, CoreStrategy, TacticalStrategy with pure signal helpers, Phase-6 EnsembleStrategy with risk-parity weights + vol-targeting; estimates module shares fee/profit calculation across strategies)
8. [x] `turbo_selector/` — filter + score + select (phase-gated) ✅ DONE 2026-05-03 (TurboCandidate (Turbo + resolved underlying); filter rules per REQ_F_TRB_002; sigmoid knockout-distance score per REQ_SDD_ALG_011; weighted total per REQ_SDD_CFG_004; phase-1 gate at `turbo_exposure_max==0`; YAML loader bridges `config/turbos.yaml`)
9. [x] `risk/` — risk engine (drawdown, position, per-trade, stop-loss enforcement) ✅ DONE 2026-05-04 (RiskEngine.pre_trade with REQ_SDD_ALG_016 gate ordering; post_trade with drawdown + Phase 5+ vol-cap escalation; SafetyLayer Protocol stub in `safety/` package; class-cap lumps STOCK + TACTICAL via `buckets_for_class`; correlation gate optional pending Phase 5 step 11 portfolio integration; single-asset cap parsed but enforcement deferred)
10. [x] `backtesting/` — deterministic engine (fees, slippage, knockouts, dividends, **tax**, injections) ✅ DONE 2026-05-08 (3 commits: 82ab5ce prerequisites; e6afec9 engine + sub-simulators; this commit walk-forward + OOS-collapse detector. SDD §6 followed: EventClock, MarketReplay with deterministic (ts, iid) ordering per REQ_SDD_ALG_019, InjectionScheduler, DividendSimulator with per-share interpretation of `Dividend.amount_gross` (SDD-deviation noted in DividendSimulator docstring; one-line wiki re-approval row appended to SDD §6.3), KnockoutSimulator, BacktestBroker thin wrapper over LocalBrokerAdapter, Backtest orchestrator. Walk-forward defaults from REQ_SDD_ALG_004; collapse threshold 0.5x train Sharpe per REQ_F_BCT_009. Throughput threshold ≥10k ticks/s held on the deterministic mock path.)
11. [x] `portfolio/` — cash, positions, gains, dividends, **after-tax equity curve** ✅ DONE 2026-05-08 (core shipped @ 82ab5ce as the step-10 prerequisite; Phase-6 attribution closed out in step 12 — RealizationEvent / DividendEvent logs, attribution() returns NAV + by-strategy + by-class AttributionRows. Dashboard hooks live in `dashboard/`.)
12. [x] `dashboard/` — phase, allocation, turbo exposure, after-tax perf, drawdown, history ✅ DONE 2026-05-08 (Analytics + Dashboard + DashboardView. Analytics wraps Portfolio + CapitalFlow read-only with equity_curve, equity_excl_injections, drawdown_series, exposure_by_class, sharpe (re-uses backtesting.walk_forward.sharpe_ratio), attribution, PerformanceSummary. Dashboard public surface is render() only — TC_LIF_002 introspection test verifies no submit / cancel / place_order / etc. methods leak per REQ_SDS_MOD_015.)

Cross-cutting (build alongside):

- [x] `safety/state_manager.py` ⇒ `NotificationFanOut` bridge (CR-001 Phase B slice 2 — REQ_F_NOT_003 / REQ_SDD_NOT_002) ✅ DONE 2026-05-17. Optional `notification_fanout: NotificationFanOut | None = None` field on `StateManager`; when wired, every KS state transition (DEGRADE / KILL trigger paths, idempotent same-state audit entries, the recovery transition) dispatches a typed `KillSwitchEvent` through the new CR-001 fan-out **after** the existing legacy `AlertChannel` path so REQ_F_NOT_003 backwards compat holds — existing deployments leaving the field unset see bit-identical behaviour. Two new builders `_ks_event_from_trigger` + `_ks_event_from_recovery` reconstruct the typed payload from the trigger / recovery context. `_normalise_severity` is the defensive boundary between the legacy free-form `KillSwitchTrigger.severity: str` and the new `KillSwitchSeverity` Literal — `DEGRADE` / `DEGRADED` both map to the canonical `DEGRADE`; unknown values panic at the boundary instead of producing an invalid `KillSwitchEvent`. Channel-failure isolation test verifies REQ_NF_NOT_001: a permanently-failing channel SHALL NOT propagate up through the state manager. 12 new tests (`tests/safety/test_notification_bridge.py`); full suite 1587 passed. REQ_F_NOT_003 + REQ_SDD_NOT_002 move TP/CODE → TEST. NOT REQs now 18 of 23 at TEST.
- [x] `notifications/loader.py` + `config/notifications.yaml` — operator-tunable notification config (CR-001 Phase B slice 1 — REQ_SDD_NOT_008) ✅ DONE 2026-05-17. `NotificationsConfig` frozen dataclass + `load_notifications_config` parser following the existing per-module loader pattern (categorised `config:io:` / `config:parse:` / `config:schema:` / `config:invariant:` Errs). Sub-shapes: `RetryConfig` mirroring `notifications.fanout.RetryPolicy` (max_attempts / base_delay_seconds / growth_factor with the same invariants); `ApprovalConfig` for the trade-approval gate (timeout_seconds / threshold_amount Decimal / threshold_currency in `Currency` enum). Closed `_CHANNEL_SELECTORS = {"local_log"}` set in v1 — Phase B extends with `email` / `whatsapp` selectors as those adapters ship. Absent file ⇒ `Ok(NotificationsConfig())` so single-deployment defaults keep working (REQ_SDS_CFG_002). `validate_all` now drives `load_notifications_config` as the 9th typed-loader entry; shape-only fallbacks added for `quant.yaml` and `webui.yaml` so the C2 startup gate catches typos in those files ahead of the typed loaders landing. Bundled `config/notifications.yaml` sample documents the v1 surface. 26 new tests (`tests/notifications/test_loader.py`); full suite 1575 passed. REQ_SDD_NOT_008 moves TP → TEST (NOT REQs now 16 of 23 at TEST).
- [x] `config/` (Python package) — centralised startup-time YAML validator (operational gap C2 from `Feature-Gap-Analysis-2026-05-16`) ✅ DONE 2026-05-16. `validate_all(config_dir) -> Result[ValidationReport, ValidationReport]` drives every shipped loader (`load_system_config`, `load_phase_engine`, `load_risk_config`, `load_kill_switch_config`, `load_turbo_selector_config`, `load_logging_config`) plus shape-only checks for the three YAMLs without typed loaders yet (`tax.yaml` / `meta_loop.yaml` / `structured.yaml`). Errs aggregate so the operator sees every bad file in one cycle (REQ_SDD_ERR_002 family). `load_system_config` extracted from `main.py._load_system_config` into a public `trading_system.config.system` module with a `SystemConfig` frozen dataclass + invariants (positive capital; non-negative seed; mode ∈ {backtest, live, paper}; non-empty broker_adapter). CLI entry: `python -m trading_system.config --validate-all --config-dir config/`. `main.py` wires the validator as a startup gate after `configure_logging` and before `run()`. 16 config tests; full suite 1299 passed. Satisfies REQ_SDS_CFG_001 ("validated at startup"). Loaders for the remaining 3 shape-only YAMLs land with the CRs that consume them.
- [x] `observability/` — structured logging infrastructure (operational gap C3 from `Feature-Gap-Analysis-2026-05-16`) ✅ DONE 2026-05-16. Stdlib `logging` + `JsonLineFormatter` emitting the REQ_SDS_CRS_001 envelope `{"ts", "category", "corr_id", "payload"}` plus the SDD §12 convenience fields (`level` / `account_id` / `module` / `message`); `LogContext` + `log_scope` contextmanager for per-tick correlation-id binding via `ContextVar` (reset-on-exception safe; nestable); `structured_log(logger, level, category, message, **payload)` convenience emitter with Decimal-as-TEXT + ISO-8601 datetime coercion (REQ_F_PER_005 family); `LoggingConfig` frozen dataclass + `load_logging_config` loader for the new `config/logging.yaml` (9th YAML, absent-file ⇒ defaults). `main.py` wires `configure_logging` at startup. `REQ_NF_LOG_001`, `REQ_SDS_CRS_001`, `REQ_SDD_LOG_001` move TP → TEST. 35 tests; full suite 1283 passed. The migration of every existing `logging.getLogger(__name__)` call to use `structured_log` is deferred — the infrastructure ships so CR-006 runtime wiring + CR-001 notification fan-out inherit the contract from day one.
- [x] `safety/` — concrete kill-switch implementation ✅ DONE 2026-05-05 (StateManager single writer per REQ_SDS_MOD_010; AuditSnapshot + MemorySnapshotSink + FileSnapshotSink for REQ_NF_AUD_001; AlertChannel Protocol with MemoryAlertChannel + deliver_with_retry exponential backoff per REQ_SDD_ERR_005; pure anomaly detectors single_day_loss_breach + rapid_decline_breach per REQ_SDD_ALG_006/007; HmacOperatorTokenVerifier + RecoveryConditions for REQ_S_KS_009; YAML loader for config/kill_switch.yaml. Standalone `monitor.py` deferred — risk engine + state manager already cover the financial trigger path; strategy / execution / integrity monitors will land alongside backtesting and execution.)
- [x] `strategy_lab/` — `generator.py`, `backtester.py`, `evaluator.py`, `risk_guard.py`, `optimizer.py`, `registry.py`, `loop_controller.py` ✅ DONE 2026-05-08 (2 commits: f13a6b5 metrics + scoring + evaluator + risk_guard + optimizer + registry; this commit generator + backtester wrapper + LoopController + integration test. Pipeline (REQ_F_MTO_002): generate → backtest → evaluate → risk_guard → walk-forward (optional) → score (REQ_F_MTO_003 weights pinned 0.4/0.3/0.2/0.1) → optimizer accept (REQ_F_MTO_006 strict-improvement comparator) → registry.store (validated=False; operator promotes via mark_validated) → ImprovementReport (REQ_F_MTO_007). Runtime imports only `strategy_lab.registry` per REQ_SDS_MOD_014; everything else is operator-driven.)
- [x] `milestone_controller/` — milestone gate + gradual exposure unlock + fake-growth detector ✅ DONE 2026-05-08 (DEFAULT_MILESTONES = 2k/5k/10k/20k/50k/100k/200k/500k/1M/2M/5M EUR per REQ_F_MIL_001; configurable via constructor. evaluate() requires every gate (stable + low_dd + consistent + no recent KS + not fake-growth) per REQ_F_MIL_002. Exposure unlock pct fixed in [0.10, 0.20] band per REQ_F_MIL_003 — exponential / leverage-explosion scaling unrepresentable. Fake-growth detector trips on any of: 30d gain > 30%, single-trade share > 50%, realized vol > 2x rolling per REQ_SDD_ALG_015. Single-shot semantics: register_crossed advances the ladder; milestones below capital_flow.initial auto-skipped.)
- [x] `structured_products/` — classifier, decomposer, regime filter, allocation cap (0–10%) ✅ DONE 2026-05-08 (admit() runs 6 gates in order: regime (BULL/SIDEWAYS only per REQ_F_STP_003/004), decomposability (REQ_F_STP_002 / REQ_SDS_MOD_008 — non-decomposable rejected before allocation math), turbo-stack ban (REQ_F_STP_007), 10% allocation cap (REQ_F_STP_001), 25% issuer concentration (REQ_F_STP_006 / REQ_SDD_ALG_014 — note: shadowed by 10% SP cap unless operator raises it for SP-heavy mandates), stress (REQ_F_STP_005 / REQ_SDD_ALG_013 — crash -20% × (1+leverage), vol -30%, corr -15%). Per-payoff decomposers for AUTOCALL / BARRIER / CAPITAL_PROT / LEV_CERT (REQ_SDD_ALG_012). Portfolio gained has_turbo_on() + issuer_concentration() helpers.)
- [x] `capital_flow/` — injection tracking, performance net of inflows ✅ DONE 2026-05-08 @ 82ab5ce (CapitalFlow ledger: total_capital, cumulative_injected_at, equity_excl_injections; observe re-sorts per REQ_SDD_ALG_017; consumed by the backtest engine's InjectionScheduler.)
- [x] `data/yfinance/` — Yahoo Finance backtest historical-data adapter (CR-009) ✅ DONE 2026-05-08 (4 commits: 90db016 cache + mappers + symbols + tests; 1f4f4d1 provider + retry + live-mode panic + tests; db7612d engine integration + JSON Lines fixtures; this commit recorder + pyproject extra + closeout. 19 new REQs reach TEST: REQ_F_DAT_001..010, REQ_NF_DAT_001, REQ_SDS_DAT_001..004, REQ_SDD_DAT_010..013. Adapter is backtest-only — constructor panics on run_mode=="live"; cache is system of record for replay determinism; yfinance + pandas behind an optional `[yfinance]` extra and lazy-imported only by the recorder script and the provider's network branch.)
- [x] `persistence/` — SQLite + thin-mapper durable state layer (CR-008) ✅ DONE 2026-05-14 (2 commits: a401caa foundation — Connection (PRAGMA-pinned WAL), MigrationRunner (SHA-locked, idempotent), 0001_init.sql (7 tables, every row carries `account_id` so CR-006 multi-account fits without schema break), Decimal/datetime mappers, PortfolioRepository; this commit RegistryRepository (HMAC-gated promotion — raw token never persisted, only SHA-256 hash + audit row), BacktestResultRepository (archive → lookup round-trips bit-identically on the replay tuple), KillSwitchSnapshotRepository (drop-in SnapshotSink Protocol replacement for FileSnapshotSink — `safety.snapshot_backend: filesystem | persistence` toggle keeps the legacy JSON-lines path available). 23 PER REQs at TEST: REQ_F_PER_001..010, REQ_NF_PER_001, REQ_SDS_PER_001..004, REQ_SDD_PER_001..008. 36 persistence tests; full suite 1001 passed.
- [x] `accounts/` — multi-account aggregate + AccountRegistry + PortfolioGroup + cross-account risk gate (CR-006, Phase 6) — foundation slice ✅ DONE 2026-05-16; **Phase A runtime wiring ✅ DONE 2026-05-16** (registry `tick(now, pipeline)` deterministic fan-out — REQ_F_ACC_002 / REQ_SDS_ACC_002 / REQ_SDD_ACC_002; `accounts.factory.build_default_registry` synthesises the legacy single-account default for the no-`accounts.yaml` path — REQ_F_ACC_003 / REQ_NF_ACC_001; `accounts.yaml_loader.load_accounts_yaml` parses the new `config/accounts.yaml` (17th YAML, optional, absent ⇒ default factory) with closed `_TAX_MODEL_SELECTORS` set + categorised `accounts:duplicate_id:<id>` Errs; `RiskEngine.pre_trade` grew an optional `cross_account_gate: Callable[[TradeProposal], Result[None, str]] | None = None` parameter wiring the existing `cross_account_concentration_gate` as gate 7 — runs *after* gates 1-6 so cheap per-account rejections short-circuit (REQ_F_ACC_008 / REQ_SDS_ACC_004); single-account default callers pass `cross_account_gate=None` and the gate is a no-op (REQ_NF_ACC_001 bit-identical). `validate_all` now drives `load_accounts_yaml` alongside the other loaders. 31 new tests (28 accounts + 5 risk; -2 from updated structural surface) across `tests/accounts/test_tick.py`, `tests/accounts/test_factory.py`, `tests/accounts/test_yaml_loader.py`, `tests/risk/test_cross_account_gate.py`. Phase B follow-up (deferred): per-account `TaxModel` dispatch (tax engine signature change); household drawdown trigger as a safety-layer subscriber; persistence call-site account_id threading; `main.py` consuming the registry for tick fan-out instead of the current direct calls. Eight modules (`account`, `registry`, `tax_model`, `group`, `cross_account_risk`, `household_drawdown_trigger`, `token_verifier`, `__init__`). Eight modules (`account`, `registry`, `tax_model`, `group`, `cross_account_risk`, `household_drawdown_trigger`, `token_verifier`, `__init__`). `Account` is a frozen aggregate (id / broker / portfolio / capital_flow / phase_engine / tax_model / risk_overlay / operator_token_account_id) with TaxModel-Protocol runtime check; `AccountRegistry` iterates lex-by-id for replay determinism; `FranceCTOTaxModel` defaults to `Decimal("0.30")` per REQ_C_TAX_001 with losses pass-through; `PortfolioGroup` aggregates household equity / exposure / drawdown via an FxConverter Protocol (IdentityFxConverter default); `cross_account_concentration_gate` no-ops for single-account registries and emits `risk:cross_account_concentration:<symbol>` on cap breach; `HouseholdDrawdownTrigger` emits KS DEGRADE @ 0.12 / KILL @ 0.15; `AccountScopedTokenVerifier` adds an account-id claim to the existing HMAC-SHA256 token (HOUSEHOLD_CLAIM sentinel for read-only fan-out; `rsplit(":", 2)` survives ISO-timestamp colons). Foundation is additive — structural tests assert `main.py` does NOT import `trading_system.accounts`, and accounts/ does NOT import `trading_system.execution` or the concrete Portfolio. TC_ACC_010 (persistence interop) verifies the bundled CR-008 migrations are idempotent + `account_id` columns isolate rows without a schema bump. 61 accounts tests; full suite 1248 passed. 23 ACC REQs at TEST: REQ_F_ACC_001..010, REQ_NF_ACC_001, REQ_SDS_ACC_001..004, REQ_SDD_ACC_001..008. **Phase B runtime wiring ✅ DONE 2026-05-18** (1 main-repo commit @ `<this commit>`). `main.py` now builds the `AccountRegistry` via `accounts.factory.build_default_registry` after `Backtest.assemble`, threading the active backtest's portfolio + capital_flow cursors into the legacy default `Account` per REQ_NF_ACC_001 backwards-compat — `PortfolioGroup` queries see live state without a separate sync. New `RunOutcome.registry: AccountRegistry | None` + `RunOutcome.household_drawdown_trip: str | None` expose the wiring to downstream consumers (the future webapp `LiveStateReader` reads through it). New `_evaluate_household_drawdown` helper invokes `PortfolioGroup` + `HouseholdDrawdownTrigger` post-`backtest.run()` and surfaces any breach severity (`DEGRADE` / `KILL` / `None`) — single-account demos stay below the 12 % degrade threshold so the demo surface stays `None` by default. New `trading_system/tax/engine_account.py` shim — `net_realized` / `net_dividend` / `for_account` route through the per-account `TaxModel` Protocol (REQ_F_ACC_005). Legacy `TaxConfig`-driven `tax/engine.py` stays in place for the single-account default; multi-account deployments use the shim. Updated structural test `test_main_py_builds_account_registry` (replacing the pre-Phase-B "main.py SHALL NOT import accounts" guard) asserts the wiring is present. New tests: `tests/test_main_registry.py` (3 — registry holds single default, portfolio refs match backtest, household trigger evaluates cleanly), `tests/tax/test_engine_account.py` (4 — France CTO default routes; losses pass through; dividend routes; custom Protocol implementation works). Full suite 1851 → 1858. Persistence call-site account_id audit deferred — current persistence repositories already require `account_id` per REQ_F_PER_009; the audit confirms call sites in `main.py` + `backtesting/engine.py` use the default account id implicitly via the single-account registry.
- [x] `notifications/` — NotificationChannel Protocol + ApprovalGate + SummaryPublisher + multi-channel fan-out (CR-001, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ a243820, SDS @ 8738c04, SDD @ ba3db40, TP @ c803b82); **Phase A implementation ✅ DONE 2026-05-16**. Seven modules + a sub-package under `trading_system/notifications/`: `payloads.py` (closed `NotificationPayload` union — KillSwitchEvent / TradeApprovalRequest / ApprovalResponse / Summary / AnomalyAlert / Error — all frozen, slotted, with non-empty + sign / bounds invariants per REQ_NF_NOT_003 minimum-necessary), `channel.py` (NotificationChannel Protocol + AlertChannel narrowed sub-Protocol — runtime-checkable; any NotificationChannel that accepts KillSwitchEvent structurally satisfies AlertChannel), `canonical.py` (canonical_json_line — sorted keys + Decimal-as-TEXT + ISO-8601 datetimes + StrEnum value form for byte-identical replays per REQ_NF_NOT_002), `channels/local_log.py` (LocalLogChannel JSON-line file writer + MemoryNotificationChannel test double; LocalLogChannel is the always-available conformance baseline), `fanout.py` (NotificationFanOut with RetryPolicy mirroring REQ_SDD_ERR_005 — max_attempts/base_delay/growth_factor; injectable sleep for tests; sorted-by-class-name observation order for deterministic logs; channel failure NEVER blocks siblings — REQ_NF_NOT_001), `approval.py` (ApprovalGate.evaluate with HMAC verification via the existing AccountScopedTokenVerifier — REQ_F_NOT_005 + account-id-claim binding per REQ_SDD_ACC_007; default-deny on timeout per REQ_F_NOT_004; `operator_token_hash` helper for Phase-B audit-row persistence; MemoryResponseInbox test double + ResponseInbox Protocol for Phase-B web-UI / persistence-backed inbox), `digest.py` (SummaryPublisher render-only — consumes PortfolioReader / AnalyticsReader / RegistryReader Protocols so the package is dependency-free of the concrete portfolio/analytics types; render() builds Summary payloads that canonicalise byte-identically per REQ_NF_NOT_002). 74 notifications tests; full suite 1479 passed. 15 of 23 NOT REQs reach TEST: REQ_F_NOT_001..002, REQ_F_NOT_004..006, REQ_F_NOT_008, REQ_NF_NOT_001..002, REQ_SDS_NOT_001, REQ_SDS_NOT_003..004, REQ_SDD_NOT_001, REQ_SDD_NOT_003..005. **Phase B follow-ups** (8 REQs still TP/CODE): `EmailNotificationChannel` + `WhatsAppNotificationChannel` concrete adapters (REQ_F_NOT_002 / REQ_SDD_NOT_007 — needs `config/notifications.yaml` 9th YAML); `safety/alert_system.py` consuming `NotificationFanOut` so the existing KS-only operator config keeps working (REQ_F_NOT_003 / REQ_SDD_NOT_002); AnomalyAlert emitters living with their upstream subsystem (REQ_F_NOT_007 / REQ_SDD_NOT_006 — touches strategy_lab + safety + execution); ApprovalGate integration in the trade-decision flow between per-account risk and order submission (REQ_SDS_NOT_002); REQ_NF_NOT_003 privacy invariant cross-cutting (verified at every emitter site); `TradeApprovalAuditRepository` persistence with SHA-256 token hash + `0002_approvals.sql` migration (CR-008 follow-up); `config/notifications.yaml` 9th YAML loader (REQ_SDD_NOT_008).
- [x] `webui/` — stdlib-HTTP API + small SPA for monitoring / summary / registry-promotion / async backtests (CR-004, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ dd82381, SDS @ d944e73, SDD @ c669f55, TP @ 691c322); **Phase A implementation ✅ DONE 2026-05-16**; **Phase B async-backtest cluster ✅ DONE 2026-05-19 @ `<this commit>`**. Five modules + a routes sub-package under `trading_system/webui/`: `auth.py` (WebAuth wrapping the existing AccountScopedTokenVerifier — require_account / require_household with HOUSEHOLD_CLAIM sentinel; reuses the categorised `registry:token_invalid` Err per REQ_SDD_ACC_007; case-insensitive Authorization-header lookup with Bearer + X-Operator-Token fallback), `schemas.py` (frozen `JsonResponse` envelope with status / canonical body / content-type; `LiveStateResponse` + `DecisionLine` + `PromoteResponse` frozen dataclasses with non-empty + sign invariants; `from_canonical` routes serialisation through `notifications.canonical.canonical_json_line` for byte-identical replays per REQ_NF_WEB_002), `idempotency.py` (IdempotencyStore Protocol + InMemoryIdempotencyStore — v1 in-memory; CR-008 SQLite repo follows the same Protocol; per-(account_id, key) lookup with lazy TTL; categorised `webui:idempotency_bad_key` / `webui:idempotency_conflict` Errs per REQ_F_WEB_008), `server.py` (Request frozen dataclass + Route + Router exact-match dispatcher + WebUIServer wrapping http.server.ThreadingHTTPServer; routes hand back JsonResponse; access logs flow through the C3 structured logger; injectable host/port=0 for ephemeral-port tests), `routes/live_state.py` (build_live_state_handler reads via a `LiveStateReader` Protocol — concrete runtime types wire in Phase B; household-claim auth; per-request canonical render), `routes/registry_promotion.py` (build_promotion_handler — the **only** mutation endpoint per REQ_F_WEB_004; idempotency check first, then auth, then delegate to a `RegistryPromoter` Protocol — the route never inlines promotion semantics per REQ_SDS_WEB_002; on success fires an `AnomalyAlert` through a `PromotionAuditNotifier` Protocol the existing `NotificationFanOut` satisfies; categorised Err → HTTP status mapping: `registry:token_invalid` → 401, `registry:strategy_not_found` → 404, `registry:already_promoted` → 409, anything else → 409). 70 webui tests across 5 files; full suite 1549 passed. 14 of 24 WEB REQs reach TEST: REQ_F_WEB_001..002, REQ_F_WEB_004..008, REQ_NF_WEB_001..002, REQ_SDS_WEB_001..002, REQ_SDD_WEB_001..004. Structural test enforces REQ_F_WEB_007 import-graph audit — no module under `trading_system/webui/` imports `execution` / `BrokerAdapter` / `LocalBrokerAdapter` / `backtesting` / `strategy_lab` / `data.mock`. **Phase B follow-ups** (10 REQs at TP): JobQueue + BacktestJobRepository for async backtest invocation (REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDS_WEB_003 / REQ_SDD_WEB_005); concrete read endpoints (financial summary, strategy registry, backtest archive, ImprovementReport history) wired into live Portfolio / Analytics / Registry types; child-process isolation kill-the-webui drill (REQ_NF_WEB_001 — Phase A wires the thread path); persistence integration — Idempotency + JobQueue rows persist through CR-008 (REQ_F_WEB_010 / REQ_SDS_WEB_004); `config/webui.yaml` 10th YAML (REQ_SDD_WEB_008); SPA bundle (REQ_SDD_WEB_005 follow-up). **Phase B async-backtest cluster** closes the 4 TP REQs (REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003) with: shared dataclasses under `trading_system/models/jobs.py` (BacktestJobSpec / BacktestJobState / JobStatus StrEnum — frozen + slotted + invariants; lives in models/ so neither webui nor persistence imports the other); `webui/job_queue.py` re-exports those + defines the `JobQueue` Protocol surface (submit / status / list_for_account) for the route layer to consume; `webui/routes/backtests.py` ships two handler builders (`build_submit_handler` returning 202 + job_id + status_url; `build_status_handler` returning the latest BacktestJobState as canonical-JSON or 404 webui:job_not_found:<id>); HTTP body validation produces categorised Errs (`webui:bad_path` / `webui:bad_request_body:config_dir` / `webui:bad_request_body:start_end` / `webui:bad_request_body:iso_datetime` / `webui:bad_request_body:with_slippage`); both endpoints require the household claim (REQ_F_WEB_005). `persistence/repositories/backtest_jobs.py` implements `SqliteBacktestJobRepository` satisfying the Protocol structurally — two-table layout (`backtest_jobs` immutable specs + `backtest_job_states` append-only transition ledger) per `0006_backtest_jobs.sql`; submit persists spec + initial PENDING transition in one BEGIN IMMEDIATE; transitions auto-increment `transition_seq` per (account_id, job_id); FAILED transitions SHALL carry an `error_category` (Err `webui:bad_transition:failed_requires_error_category`); `claim_next_pending` is the worker hook (atomically picks the oldest PENDING row → RUNNING). Duplicate (account_id, job_id) → `persistence:integrity:backtest_jobs:duplicate:<id>`. 22 new tests (12 repo + 10 route); webui + persistence suite 182 passed. The in-process thread worker drainer is deferred — operators with the CR-017 FastAPI webapp use the existing `InProcessJobQueue` (ProcessPoolExecutor); stdlib-webui operators wire `SqliteBacktestJobRepository` at server construction and run a separate `claim_next_pending` daemon when ready.
- [x] `strategy_lab/quant/` — Hypothesis layer with five-gate validator + overfitting-aware StrategyMetrics + ImprovementReport.hypothesis_ids traceability (CR-002, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ 88f9569, SDS @ 222c83c, SDD @ 8b78c4a, TP @ de0470d); **Phase A implementation ✅ DONE 2026-05-16**. Five modules under `trading_system/strategy_lab/quant/`: `hypothesis.py` (Hypothesis frozen dataclass + HypothesisState/Direction StrEnums + DatasetWindow + HypothesisResult with rejection_reason/outcome invariants), `validator.py` (HypothesisValidator with 5 gates in strict order — structural / bounds / falsifiable / metric-alignment / dataset-sanity; configurable `ValidatorConfig.bounds_table` + `metric_vocabulary` + `min_duration_days_for_1d` + `min_window_for_intraday_days`; closed Err category set), `library.py` (HypothesisLibrary over a HypothesisStore Protocol; v1 ships `InMemoryHypothesisStore` — the CR-008 SQLite-backed repository follows the same Protocol; append-only with separate `TransitionRecord` audit log; deterministic created_at-sorted iteration per REQ_NF_QNT_002), `overfitting.py` (pure functions: `parameter_to_data_ratio` returns `Decimal("Infinity")` for degenerate denominators, `adjusted_sharpe` with Decimal.sqrt, `information_coefficient` Pearson over the `(sharpe, return, drawdown)` triple — returns Decimal("0") on zero-variance degeneracy, `overfitting_gate` with documented `DEFAULT_RATIO_MAX=0.10` / `DEFAULT_IC_FLOOR=0.30` thresholds), `runner.py` (HypothesisRunner orchestrating validator → backtester → evaluator → library transition; `BacktesterAdapter` + `EvaluatorAdapter` Protocols so the existing strategy_lab.backtester wires in via thin Phase-B wrappers; `DefaultEvaluator` runs the overfitting gate + Direction check). `StrategyMetrics` extended with three new fields (`n_params`, `n_train_periods`, `information_coefficient`) — defaults preserve backwards-compat. 75 quant tests; full suite 1405 passed. 16 of 20 QNT REQs reach TEST: REQ_F_QNT_001..004, REQ_F_QNT_006, REQ_NF_QNT_001..002, REQ_SDS_QNT_001..004, REQ_SDD_QNT_001..005. **Phase B follow-ups** (4 REQs still at TP/CODE): `ImprovementReport.hypothesis_ids` tuple (REQ_F_QNT_005); optimizer integration that substitutes adjusted_sharpe into the score (REQ_SDD_QNT_006); ImprovementReport persistence with hypothesis_ids (REQ_SDD_QNT_007); `config/quant.yaml` 11th YAML (REQ_SDD_QNT_008); loop_controller step-0 hook draining PENDING hypotheses; CR-001 AnomalyAlert wiring on REJECTED transitions; 0004_quant.sql migration for the CR-008 HypothesisRepository SQLite backend. Offline-only invariant (REQ_NF_QNT_001) enforced by `tests/strategy_lab/quant/test_structural.py` — no runtime module SHALL import `strategy_lab.quant`.
- [x] `portfolio_manager/` — proposal generators + multi-scope attribution (CR-005, algorithmic core) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 3fa884b, SDS @ ae6dc93, SDD @ 8795e3d, TP @ 878309b, closeout). `Rebalancer.propose` (drift-from-target proposals; alphabetical AllocationBucket.value ordering; non-strict band on the edge), `SectorRotatorFacade` (wraps CR-010 RotationProposal → per-instrument TradeProposal; no-op when empty for phase-1..4), `TaxHarvesterFacade` (wraps tax/harvest.py HarvestSuggestion → SELL TradeProposal; silently drops stale non-held suggestions), `AttributionDecomposition` (by_strategy / by_sector / by_class / by_region; each scope sums to NAV ± 1e-9 — enforced at construction). Read-only over Portfolio (AST audit asserts no portfolio_manager/ module imports the Portfolio mutators or execution layer). 37 tests; 14 PMG REQs at TEST: REQ_F_PMG_001..008, REQ_SDS_PMG_001..002, REQ_SDD_PMG_001..004. Runtime wiring (strategies → portfolio_manager → risk) deferred to Phase-6 follow-up alongside CR-006 multi-account.
- [x] `tests/integration/test_phase5_stack.py` — Phase-5 integration drill ✅ DONE 2026-05-15 — one end-to-end test that composes the shipped Phase-5 CRs (CR-008 persistence + CR-013 regime + CR-014 fundamentals + CR-015 rationales + CR-011 fx_hedger) in a single linear pipeline. Builds a `CompositeFundamentalsProvider(CSV + Mock)`, runs the screener through it, classifies a synthetic BULL bar series via `RegimeDetector`, computes FX exposure + proposes hedges over a multi-currency portfolio, opens/closes a forward through `FXHedgeLedger`, assembles a `BacktestResult` with aligned `TradeRationale` rows, and archives → reads back through `BacktestResultRepository` asserting bit-identical equality. Breaks loudly if any wiring drifts. The cheapest insurance against the kind of composition-regression that per-CR unit suites miss; runs in <1s.
- [x] `wealth_ops/fx_hedger/` — Phase-5 currency hedger (CR-011) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 9ea3637, SDS @ 1936020, SDD @ 45d94f0, TP @ 2be25a8, closeout). Pure-function `compute_fx_exposure` + `FXHedger.propose_hedges` (strict-above-threshold; deterministic Currency.value ordering); frozen `HedgePolicy` / `HedgeProposal` / `FXForward` / `MarkedPosition` row shapes; append-only `FXHedgeLedger` with deterministic mark formula `notional × (current_fx_rate / entry_fx_rate - 1)`; tax treatment per REQ_F_FXH_006 (gains × 0.70, losses pass through). Separate ledger — `InstrumentClass.FX` enum NOT extended; existing consumers untouched. CR-008's `FXHedgeLedgerRepository` slot is the live-mode target for a Phase-6 follow-up. 50 tests; 14 FXH REQs at TEST: REQ_F_FXH_001..006, REQ_NF_FXH_001, REQ_SDS_FXH_001..002, REQ_SDD_FXH_001..005. Closes the Phase-5 implementation queue from CLAUDE.md.
- [x] `models/rationale.py` + `analytics/rationale.py` — Trade rationale audit trail (CR-015) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 0575943, SDS @ 2772a3b, SDD @ 73aa62f, TP @ 92873b4, closeout). `TradeRationale` frozen dataclass with REQ_F_RAT_002 shape (trade_id / strategy_id / strategy_version / signal_reason / risk_approval / tax_gate_decision / improvement_report_id / decided_at); non-empty trade_id+strategy_id invariants; hashable + structurally equal across Mapping types; `GATE_VOCABULARY` constant + `validate_gate_vocabulary` audit helper for the closed gate-name set `{tax_gate, kill_switch, risk_per_trade, stop_loss, class_cap, correlation, regime, cross_account_concentration}`. `BacktestResult.rationales: tuple[TradeRationale, ...] = ()` defaulting to empty for backwards compat (length-aligned with trades when non-empty). `analytics.rationale_for(result, trade_id) -> Option[TradeRationale]` is the public read surface. CR-008's persistence mapper (`backtest_result_to_json` / `_from_json`) extended to round-trip rationales bit-identically; older archives without the field round-trip as empty (TC_RAT_010 verifies). 27 tests; 9 RAT REQs at TEST: REQ_F_RAT_001..005, REQ_SDS_RAT_001, REQ_SDD_RAT_001..003.
- [x] `data/fundamentals/` — CSV-seeded fundamentals provider (CR-014) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 6cd233b, SDS @ c13bfab, SDD @ f87821f, TP @ 7e19ab6, closeout). `CSVFundamentalsProvider` loads `data/seed_fundamentals.csv` at construction (14 EU dividend stocks), validates schema + numeric + as_of_date + duplicate-id, freezes the snapshot, supports `fundamentals()` only — every other MarketDataProvider method returns `data:not_supported:csv_only` so a mis-wired caller fails fast. `CompositeFundamentalsProvider` chains 2+ providers, first-Ok wins, last-Err loses; empty composite surfaces `data:not_supported:composite_empty`. `tools/fundamentals_csv_template.py` emits a header-only stub for new universes. Refresh hook for operator tooling. 26 tests; 10 FND REQs at TEST: REQ_F_FND_001..005, REQ_NF_FND_001, REQ_SDS_FND_001, REQ_SDD_FND_001..003.
- [x] `regime/` — Market-regime detector + transition tracker (CR-013) ✅ DONE 2026-05-15 (2 commits: bb25b71 foundation — RegimeDetector with public RULE_ORDER + MA-crossover + vol-band rule, TransitionTracker with single mutable cursor + confirmation window + flip-back reset + `from_seed` restart hook, RegimeConfig with invariants, BarSource Protocol; this commit closeout — RegimeOrchestrator wires detector → tracker → SafetyLayer.raise_trigger + TransitionRepository persistence + ast audit forbidding KillSwitch.set_state, CR-008 follow-up TransitionRepository + 0002_regime.sql migration + transition_event_to_row / row_to_transition_event mappers, restart rehydration via repo.latest() + TransitionTracker.from_seed). 14 RGM REQs at TEST: REQ_F_RGM_001..006, REQ_NF_RGM_001, REQ_SDS_RGM_001..002, REQ_SDD_RGM_001..005. 45 regime + transition-repo tests; full suite 1046 passed. The detector is now the runtime's sole MarketRegime source — consumers (sector_rotator, structured_products, risk-engine regime gate) receive the regime as input through the orchestrator's tick boundary instead of computing or hand-setting their own.
- [x] `analytics/` — performance + monitoring; phase 6 NAV/attribution reporter ✅ DONE 2026-05-23 @ `<this commit>`. New `trading_system/analytics/attribution.py` ships `StrategyAttribution` + `AttributionReport` frozen dataclasses + a pure `attribution_from_result(BacktestResult) -> AttributionReport` function. Walks every Trade, looks up its TradeRationale → strategy_id, accumulates per-strategy trade_count + total_turnover + total_fees + turnover_share_pct + realized_pnl_proxy. v1 is notional-weighted (cost-basis matching deferred; needs a position-history time series the engine doesn't surface yet). Orphan trades land under the documented `"unknown"` sentinel. Rows sorted by strategy_id ASC for replay determinism. Wired into the webapp's `_default_worker` so every backtest run emits `attribution.json` next to the CR-016 5-file bundle (canonical JSON for diff-friendliness). `/reports/<job_id>` view loads + renders a "Per-strategy attribution" card with the portfolio totals + per-strategy table. UI polish bonus: the live-state Phase tile now shows the documented `REQ_F_CAP_003` label (`1 · Capital Builder`, `2 · Stability`, …) instead of the bare numeric phase. 8 new tests (6 attribution unit + 2 reports view + 1 worker integration); full suite 411 pass.
- [~] `wealth_ops/` — phase-5 features: tax-loss harvester, sector rotator, currency hedger — **tax-loss harvester** shipped 2026-05-08 under `tax/harvest.py` (REQ_F_TAX_006 at TEST). **Sector rotation** shipped 2026-05-14 via CR-010 (Done) under `wealth_ops/sector_rotator/` — taxonomy + regime_sector_bias + RotationPolicy + HoldingState + SectorRotator; 18 REQs at TEST (REQ_F_SCT_001..007, REQ_NF_SCT_001, REQ_SDS_SCT_001..003, REQ_SDD_SCT_001..007). **Currency hedging** shipped 2026-05-15 via CR-010-style cascade under `wealth_ops/fx_hedger/` (CR-011 Done — see entry above); 14 FXH REQs at TEST.
- [~] `institutional/` — phase-6 features: vol-target sizer, risk-parity allocator, strategy ensemble, hedge-overlay manager — partial 2026-05-08: **vol-target sizer** + **risk-parity allocator** + **strategy ensemble** all live in `strategies/ensemble.py` (REQ_F_STR_004 / REQ_SDD_ALG_010 at TEST). **NAV / attribution reporting** lives in `portfolio.attribution()` + `analytics.Analytics` (REQ_F_PRT_002 at TEST). **Hedge-overlay manager (CR-012)** ✅ DONE 2026-05-17 (4 wiki commits + 1 main-repo commit: SRS @ `4db591e`, SDS @ `7b4f2d9`, SDD @ `3e901db`, TP @ `cd6cda6`; code @ `<this commit>`). All 12 REQs at TEST: REQ_F_HOV_001..005, REQ_NF_HOV_001, REQ_SDS_HOV_001..002, REQ_SDD_HOV_001..004. Six modules under `trading_system/institutional/hedge_overlay/`: `errors.py` (closed `OverlayError` category set — `hov:insufficient_history:<o>/<r>` / `hov:degenerate_benchmark` / `hov:not_found:<id>` / `hov:already_closed:<id>` + informational `hov:phase_below_6` / `hov:band_satisfied` / `hov:cap_exceeded`), `policy.py` (frozen `OverlayPolicy` with **hard ≤ 10 % `max_overlay_pct` ceiling per REQ_F_CAP_011** — operators MAY tighten but SHALL NOT loosen; defaults pinned to the SDD: `target_beta=0.5`, `target_vol=0.12`, `beta_band=0.05`, `hedge_ratio=1.0`, `rebalance_frequency="weekly"`, `max_overlay_pct=0.10`, `benchmark="EUROSTOXX50"`, `carry_pct_per_year=0.005`), `instruments.py` (frozen `IndexFuturePosition` with OPEN↔CLOSED field invariants + positive `entry_index_level` + frozen `OverlayProposal` with `notional > 0` invariant + `OverlayPositionState` StrEnum — **NO `InstrumentClass.OVERLAY` enum extension** per REQ_SDS_HOV_001), `exposure.py` (pure `compute_portfolio_beta` — rolling 60-period beta against benchmark; `window < 2` panics with `ValueError("hov:bad_window:<n>")`; categorised `Err("hov:insufficient_history:<observed>/<required>")` / `Err("hov:degenerate_benchmark")` on zero-variance), `overlay.py` (pure `HedgeOverlay.size` — **phase gate FIRST** per REQ_SDS_HOV_002, sub-Phase-6 returns `Ok(())` before reading current_beta/policy/household_equity; band check returns `Ok(())` when `|beta_delta| <= beta_band`; clamp `notional = min(raw_notional, max_overlay_pct × household_equity)` enforces the 10 % cap; at most one `OverlayProposal` per call), `ledger.py` (append-only `OverlayLedger` with single mutable `_positions` cursor; `open` assigns monotonic ids; `close` returns categorised Errs; deterministic mark formula `notional × (current_index_level / entry_index_level - 1)` with no rounding; deterministic carry `notional × carry_pct_per_year × elapsed_days / 365`; `realized_pnl_after_tax` applies `gain × (1 - tax_rate)` for `pnl > 0`, passes losses through unchanged per REQ_C_TAX_001 family — matches CR-011 FX-hedger tax semantics). 47 new tests (`test_policy.py` 9 + `test_instruments.py` 8 + `test_exposure.py` 9 + `test_overlay.py` 9 + `test_ledger.py` 12 + `test_structural.py` 3); full suite 1732 → 1779. Closed import-graph: `institutional/hedge_overlay/` SHALL import only from `models` / `portfolio` / `tax` / `result` (no `risk` / `execution` / `safety` / `strategy_lab` / `accounts` / `notifications` / `webui` / `backtesting.monte_carlo`) — TC_HOV_010 AST audit enforces; same audit verifies `models/instrument.py` has no `OVERLAY` value. v1: **index futures only** (linear delta hedge — option overlays defer to a follow-up sub-CR); EUR-denominated overlays only (CR-011 coordination deferred). CR-008 persistence — `OverlayLedgerRepository` slot + `0006_overlay.sql` migration + `institutional.overlay_archive: memory | persistence` toggle remain Phase-6 follow-ups. CR-007 → CR-012 follow-up sub-CR (overlay sizer consumes MC shock distributions through `MonteCarloRunner` to size against tail beta) opens now that both implementations ship.
- [x] `backtesting/monte_carlo/` — Monte Carlo simulation (CR-007, Phase 5) ✅ DONE 2026-05-17 (4 wiki commits + 1 main-repo commit: SRS @ `e3564d1`, SDS @ `2b84446`, SDD @ `958e63c`, TP @ `71a821d`; code @ `<this commit>`). All 17 REQs at TEST: REQ_F_MCS_001..006, REQ_NF_MCS_001, REQ_SDS_MCS_001..004, REQ_SDD_MCS_001..006. Six modules under `trading_system/backtesting/monte_carlo/`: `errors.py` (closed `MonteCarloError` category set — `mc:config_mismatch:<field>` / `mc:n_paths_out_of_bounds` / `mc:bad_block_length` / `mc:empty_history` / `mc:generator_failed:<reason>`), `config.py` (frozen `MCConfig` with `n_paths ∈ [100, 100_000]` + closed `generator` Literal cross-checking generator-specific fields; `GBMParams` validator forbids negative sigma; `RNGSeed` NewType), `result.py` (frozen `MonteCarloResult` with `QUINTILE_KEYS = (0.05, 0.25, 0.50, 0.75, 0.95)` closed keyset + monotonicity invariant on every percentile mapping + `kill_switch_trip_rate` ∈ `[0, 1]` + non-empty `config_hash` enforced at construction — panic `mc:percentile_invariant:<field>:<reason>`), `generator.py` (single-method `MCGenerator` Protocol + `BlockBootstrapGenerator` (i.i.d. block resampling) + `GBMGenerator` (`r_t = mu + sigma * z_t`) + `RegimeStitchedGenerator` (per-regime sub-blocks via injected detector with `default`-label fallback) + pure helpers `_historical_returns` / `_step_delta` / `_materialise_path` rebuilding bars from returns with `open == high == low == close` + `percentile` linear-interpolation + `stddev_decimal`), `runner.py` (`MonteCarloRunner` composes via injected `backtest_factory: Callable[[Strategy, tuple[Bar, ...]], Backtest]` — NEVER forks `engine.py`; per-path RNG via `seed_for_path(seed, i) = int.from_bytes(sha256(seed||i)[:8], "big")` big-endian formula; `_aggregate` produces full percentile maps + KS-trip-rate proxy from `BacktestResult.knockouts > 0` OR `max(drawdown_pct) >= 0.25`; `config_hash = sha256(canonical_json(MCConfig))` for the CR-008 archive join key). 43 MC tests + 5 loop_controller MC tests = 48 new tests; full suite 1685 → 1732. Meta-loop integration via `strategy_lab/loop_controller.py` — new optional `mc_run_step: MCRunStep | None = None` + `mc_drawdown_floor: Decimal | None = None` fields gate the cycle after walk-forward + before evaluator; `mc_run_step is None` ⇒ gate bypassed (TC_MCS_008 spy verifies); P5 drawdown > floor ⇒ candidate rejected with `"mc:p5_drawdown_exceeds_phase_floor"`. v1 KS-trip proxy is `knockouts > 0` OR `max_drawdown >= 0.25`; Phase-B follow-up threads the actual `KillSwitchState` snapshot through `BacktestResult`. Closed import-graph: `monte_carlo/` SHALL NOT import `execution.*` / `risk.*` / `safety.*` / `strategy_lab.*` / `portfolio_manager.*` / `accounts.*` / `notifications.*` / `webui.*` / `webapp.*` — TC_MCS_009 AST audit enforces. CR-008 persistence — `MonteCarloResultRepository` slot + `0005_mc.sql` migration deferred to Phase-6 (REQ_SDD_MCS_006 wire-up plumbing). Sequenced *before* CR-012 so the hedge-overlay sizer can consume MC shock distributions in its follow-up sub-CR.
- [x] `analytics/report.py` + `trading_system/cli.py` — MVP Hardening v1: report artefacts + operator CLI (CR-016) ✅ DONE 2026-05-17 (5 wiki commits + 5 main-repo commits: SRS @ ba7b70e, SDS @ 8c1f96f, SDD @ 5015ef0, TP @ 5411e59, MVP-1 fixtures @ 23a1660, MVP-2+3 data factory + universes @ c5fe390, MVP-4+5 report + CLI @ 73bd0cb, MVP-6 Quickstart + baseline report @ 1885cc8, **Phase B `main.run` → `RunOutcome` + CLI write_report wiring @ <this commit>**). 14 RPT/CLI REQs at TEST: REQ_F_RPT_001..003, REQ_NF_RPT_001, REQ_O_004, REQ_SDS_RPT_001..002, REQ_SDS_CLI_001..002, REQ_SDD_RPT_001..003, REQ_SDD_CLI_001..002. Six-item MVP-v1 critical path complete + Phase B closes the report-emission loop: bundled offline yfinance fixtures (3 EU dividend stocks, 2024 weekday closes) so the recorder isn't network-required; config-driven `DataProviderConfig` factory + `_build_data_provider` chaining (yfinance + CSV fundamentals composite); two shipped universe presets (`eu-dividend-starter` + `cac40` subset) with alphabetical-by-id invariant; pure `write_report` emitting the closed 5-file artefact set (`trades.csv` 10-column closed header + `(executed_at, trade_id)` sort; `equity-curve.html` static base64-embedded PNG with no JS / no network; standalone `equity-curve.png` at deterministic `figsize=(10,4)` `dpi=100`; `summary.json` 12-key shape; `manifest.json` 7-key shape with `REPORT_SCHEMA_VERSION="1"` + `png_sha256`); argparse `trading-bot` console script with 3 subcommands (`backtest` / `record-data` / `validate-config`) registered via `[project.scripts]`; pre-recorded `var/reports/2024-baseline/` so a fresh clone sees the expected output shape before running anything; `Documentations/Quickstart.md` for the 5-minute path; **Phase B reshapes `main.run` to return `Result[RunOutcome, str]` where `RunOutcome` carries `view + result + config_hash + seed + data_provider` so the CLI's `backtest` handler emits the report directory via `write_report` end-to-end (`--report-dir <path>` flag; default `var/reports/<utc-iso-timestamp>/`). `config_hash` is SHA-256 over the canonicalised replay-tuple (starting capital, seed, mode, broker_adapter, data provider settings, start/end, timeframe, slippage flag) matching the CR-008 BacktestResultRepository convention.** Plumbing-only invariants enforced via AST audits (REQ_SDS_CLI_001 / REQ_SDD_CLI_002 — CLI SHALL NOT import execution / safety / risk / strategy_lab; analytics/ SHALL NOT reach decisioning layers). 1685 tests pass.
- [x] `trading_system/webapp/` + `Dockerfile` — FastAPI webapp + container deployment (CR-017) — design cascade complete 2026-05-17 (4 wiki commits: SRS @ `3538073`, SDS @ `d5d8537`, SDD @ `9dbb1fe`, TP @ `ccf02d4`). **Phase A implementation ✅ DONE 2026-05-17** + **Phase B closeout (CR-017 ✅ Done) 2026-05-20 @ `<this commit>`** — all 20 FAS REQs at TEST after `REQ_NF_FAS_002` lands (see Phase-B addendum at the end of this row). 16 of 20 REQs at TEST: REQ_F_FAS_001/002/004/005/007 + REQ_NF_FAS_001 + REQ_SDS_FAS_001..004 + REQ_SDD_FAS_001..004/006/007. Six modules under `trading_system/webapp/`: `app.py` (create_app factory + default_app entry consumed by the Dockerfile + WebappState bag), `canonical.py` (canonical_json_response + canonical_error_response wrapping the project-wide `notifications.canonical.canonical_json_line` so the FastAPI surface emits **byte-identical bytes** to the stdlib webui/ path — preserves REQ_NF_FAS_001 by re-using the existing well-tested serialiser rather than coupling replay to pydantic's encoder), `health.py` (GET /health for the container HEALTHCHECK; reads TRADING_BOT_VERSION from env), `auth_deps.py` (FastAPI DI wrappers — `require_household` for read endpoints, `require_account_token` for mutations; HTTP-only cookie sessions deferred to Phase B), `routers/api/live_state.py` (GET /api/accounts/{account_id}/live-state — household gate; LiveStateReader Protocol slot on app.state), `routers/api/registry.py` (POST /api/registry/{strategy_id}/promote — per-account gate; pydantic-validated body; RegistryPromoter Protocol + PromotionAuditNotifier slot; Err category → HTTP status mapping mirrors stdlib path: token_invalid→401, strategy_not_found→404, already_promoted→409), `routers/views/dashboard.py` (GET / — HTMX dashboard polling /api/accounts/default/live-state every 5s; **no SPA bundle, no Node toolchain**; bundled htmx.min.js placeholder under static/ with documented operator step to fetch the 1.9.10 release). Templates: base.html + dashboard.html under templates/. Top-level: multi-stage Dockerfile (builder ⇒ runtime on `python:3.12-slim-bookworm`; ARG BASE_TAG pins version; useradd uid 10001 trading + USER trading; EXPOSE 8000; HEALTHCHECK via stdlib urllib; ENTRYPOINT uvicorn + CMD `default_app --factory`; runtime stage SHALL NOT install build-essential/gcc/libssl-dev/libffi-dev — structural test enforces; apt-get clean + rm -rf /var/lib/apt/lists/* after every install), `.dockerignore` (excludes .git/.venv/Documentations/tests/tools/.cache/var/), `compose.yaml` (webapp service + trading-data named volume + TRADING_BOT_OPERATOR_SECRET env injection). pyproject.toml adds `[webapp]` extra: fastapi>=0.110 + uvicorn[standard]>=0.27 + pydantic>=2.5 + jinja2>=3.1 + sse-starlette>=2.0 + python-multipart + httpx (for TestClient). OpenAPI schema-stability — `tools/regenerate_openapi_snapshot.py` regenerates `tests/webapp/openapi_phase_a.expected.json`; the snapshot test fails on drift forcing a Test-Plan re-approval row per REQ_NF_LIF_002 (REQ_SDD_FAS_006 — the only test that intentionally fails when a schema-shape commit lands without operator review). 49 webapp tests (`test_app.py` 6 + `test_canonical.py` 6 + `test_routes_api.py` 11 + `test_routes_views.py` 3 + `test_structural.py` 5 + `test_dockerfile.py` 15 + `test_openapi_stability.py` 3); full suite 1779 → 1828. **Phase B follow-ups** (4 REQs still TP): REQ_F_FAS_003 SSE live-state push at /events/live-state via sse-starlette + dashboard hx-ext="sse" + sse-connect; REQ_F_FAS_006 + REQ_SDD_FAS_005 async backtest invocation via InProcessJobQueue (ProcessPoolExecutor) + POST /api/backtests returning 202+job_id + SSE-streamed progress; REQ_NF_FAS_002 container reproducibility — requires `requirements.lock` (pip-compile --generate-hashes), Dockerfile pinned by `BASE_DIGEST=sha256:<...>`, and a CI build step asserting digest stability across runs. HTMX cookie-session auth + the other CR-004 Phase B endpoints (summary / registry-list / backtests-archive / improvement-reports-history) also land in Phase B. Stdlib `webui/` path stays as the no-dependencies fallback. **Phase B closeout 2026-05-20** — `REQ_NF_FAS_002` lands at TEST: Dockerfile pins both stages by `BASE_DIGEST=sha256:d193c6f5...` (REQ_SDD_FAS_007 ARG pattern); `requirements.lock` (51 KB, 32 distributions × 594 sha256 hashes; pip-compile --generate-hashes output) is the source of truth for runtime deps; `pip install --require-hashes --no-deps` in the builder stage + `pip install --no-index` in the runtime stage (offline replay from the builder's wheels). New top-level `ARG SOURCE_DATE_EPOCH` declared in both stages so BuildKit honours timestamp rewriting → tar-entry mtimes in image layers are bound to a fixed epoch and content-hashes are stable across rebuilds. 6 new static reproducibility tests in `tests/webapp/test_dockerfile.py` (base-digest sha256 format, both stages consume BASE_DIGEST, lockfile pip-compile-generated, every dist pinned with ≥ 1 sha256 hash, `--require-hashes` present, runtime install is `--no-index`). 2 new docker-marked dynamic tests in `tests/webapp/test_container_reproducibility.py` (TC_CONT_004: two builds with same SOURCE_DATE_EPOCH produce equal RootFS layer digests; TC_CONT_005: tampered lockfile fails the build with pip's "hashes do not match" error). Docker marker registered in `pyproject.toml` so the daemon-dependent tests opt in via `pytest -m docker`. Full webapp suite 93 passed (91 static + 2 dynamic). CR-017 fully closed; 20/20 FAS REQs at TEST.
- **Phase-8 operator hardening sprint** (Gap-Analysis-2026-05-23 Part C) — in progress.
  - [x] **C2 — Structured logging + correlation IDs** ✅ DONE 2026-05-23 @ `<this commit>`. Ships `trading_system/webapp/middleware.py` (`CorrelationMiddleware` binds a fresh `LogContext` per request; `X-Request-ID` round-trip; per-request `account_id` extracted from `/api/accounts/<aid>/...` and `/paper-sessions/<aid>/...` path patterns), wires `app.add_middleware(CorrelationMiddleware)` in `create_app()`, and calls `configure_logging(level=..., json_output=...)` at the top of `default_app()` so production boots emit JSON-line logs by default (`TRADING_BOT_LOG_HUMAN=1` flips to the human format for local tailing). `trading_system.observability.__init__` exports `current_context()` so handlers + tests can read the bound `LogContext`. Structural audit allow-listed `trading_system.observability` (log infrastructure; engine-state-free). 8 new tests at `tests/webapp/test_correlation_middleware.py`: fresh corr_id generation; client-supplied id round-trip; account_id extraction from API + paper-sessions + unscoped paths; LogContext visibility inside handlers; full JSON-line round-trip; no leak between sequential requests. Anchors REQ_NF_LOG_001 + REQ_SDS_CRS_001.
  - [x] **C1 — Coverage cleanup (fourth strike: persistence repos round 2)** ✅ DONE 2026-05-25 @ `<this commit>`. Closes out the remaining persistence repository coverage gaps using the same `_RaisingExecProxy` injection pattern from the third strike. 5 files lifted: `portfolio.py` 55% → **96%** (8 new tests — operational/corrupt categories on `append_equity_point`; equity_curve read DB error + parse error from a tampered row; list_account_ids_with_prefix happy path + empty-prefix rejection + DB error; dual-fault rollback); `idempotency.py` 78% → **100%** (8 new tests — constructor TTL invariant + lookup/record read/write DB errors + status_code_for swallow-and-return-None on DB error + missing returns None + lazy-DELETE failure on expired entry + sweep_expired DB error); `transition.py` 74% → **100%** (7 new tests — operational/corrupt categories on append + latest read DB error + latest parse error from a tampered row with BOGUS regime + history read + parse errors + dual-fault rollback); `approvals.py` 74% → **97%** (7 new tests — record_request generic DB error + record_response integrity/generic DB errors + get_request/get_response DB errors + get_response missing returns Nothing + verify_token propagates read Err); `quant.py` 75% → **98%** (10 new tests — append generic DB error + record_transition propagates read err + integrity/corrupt during INSERT + get/list_all/transitions_for DB errors + current_state propagates read err + missing-hypothesis Nothing path + transitions read DB error). 40 new tests across 5 files; full suite 2 644 → 2 684. Persistence repos overall: 83% → ~98% on the 5 repos in scope (+15pp). No new REQs (test-evidence entries reinforce existing REQ_F_PER_002 / 003 / 008 / 009, REQ_NF_PER_001, REQ_F_NOT_004 / 005, REQ_NF_NOT_003, REQ_NF_RGM_001, REQ_F_WEB_010, REQ_NF_QNT_002, REQ_SDD_PER_002 / 008, REQ_SDD_WEB_004, REQ_SDD_RGM_005).
  - [x] **C1 — Coverage cleanup (third strike: persistence repository Err branches)** ✅ DONE 2026-05-23 @ `<this commit>`. Three repository files pulled to near-100% via 26 new Err-branch tests + a reusable `_RaisingExecProxy` injection pattern (wraps `Connection._raw` with a delegating proxy that raises a chosen `sqlite3.Error` subclass on matching SQL — needed because `Connection.execute` is read-only on the slotted dataclass and the underlying `sqlite3.Connection.execute` is also non-monkey-patchable). `trading_system/persistence/repositories/snapshot.py` 62% → **98%** (6 new tests: IntegrityError → `persistence:integrity:`; OperationalError → `persistence:locked:`; generic DatabaseError → `persistence:corrupt:`; `record` panics on write failure per REQ_SDD_PER_007; `get` DatabaseError → `persistence:corrupt:...:read:`; `_safe_rollback` swallows secondary rollback errors). `trading_system/persistence/repositories/backtest.py` 66% → **100%** (5 new tests covering the same three exception → category map on `archive` + `lookup` DatabaseError + `_safe_rollback` dual-fault). `trading_system/persistence/repositories/registry.py` 66% → **98%** (15 new tests covering get/list_validated/list_experimental DatabaseError + the previously-uncovered `list_experimental` happy path itself + `store` read-Err propagation from `get` + store integrity/operational/corrupt categories + `request_promotion` read-Err propagation + integrity/operational/corrupt during the UPDATE+INSERT pair + `promotion_audit` chronological reader smoke + `promotion_audit` read DatabaseError + `_safe_rollback` dual-fault). Strengthens REQ_F_PER_006, REQ_F_PER_007, REQ_F_PER_008, REQ_NF_PER_001, REQ_SDS_PER_002, REQ_SDD_PER_005, REQ_SDD_PER_006, REQ_SDD_PER_007 — no new REQs (existing requirements gain test-evidence entries). Full suite 2 618 → 2 644.
  - [x] **C1 — Coverage cleanup (second strike: universes + job-models + cac40 fix)** ✅ DONE 2026-05-23 @ `<this commit>`. Closes a pre-existing test failure + lifts two more financial-logic files: `tests/data/test_universes.py::test_load_cac40_returns_expected_subset` was asserting the legacy 3-stock subset (`ASML.AS`/`BNP.PA`/`SAN.PA`) which broke when the cac40 universe was rewritten to ship real CAC 40 constituents (ASML belongs to AEX not CAC 40). Updated to probe a 5-symbol household-name subset (`AIR.PA`/`BNP.PA`/`MC.PA`/`SAN.PA`/`TTE.PA`). `trading_system/data/universes.py` 71% → **97%** via 11 new Err-branch tests covering: non-string description / non-list stocks / non-mapping stock entry / non-string id / non-string currency / non-string symbol / Stock construction invariant (empty symbol) / list_bundled_universes missing-root case. `trading_system/models/jobs.py` 77% → **100%** via 14 new tests at `tests/models/test_jobs.py::TestBacktestJobSpec` (8 tests — minimal-spec construct; default for with_slippage + account_id; empty job_id / config_dir / account_id rejected; whitespace-only job_id rejected; end-before-start rejected; end-equals-start accepted as zero-length window; frozen dataclass smoke) + `::TestBacktestJobState` (5 tests — minimal pending state; empty job_id rejected; FAILED status requires error_category; FAILED with category accepted; frozen smoke) + `test_job_status_values` (locks the StrEnum canonical strings). 26 tests in `tests/data/test_universes.py` (16 existing + 11 new — actually the test_load_cac40 fix unlocked the file to run in CI ⇒ the existing 17 lifted to test coverage too) + 14 tests in `tests/models/test_jobs.py`. Side-effect: 547 → 547 REQs unchanged; 2 576 → 2 618 passing.
  - [x] **C1 — Coverage cleanup (first strike: financial-logic invariant validators)** ✅ DONE 2026-05-23 @ `<this commit>`. Targeted Err-branch tests pulling two financial-logic files from below 90% to **100% coverage** in one slice — `trading_system/structured_products/decomposition.py` 69% → 100% (Decomposition's four-field invariant validators: negative equity_equiv, negative hidden_leverage, worst_case_loss outside [0,1], break_even_prob outside [0,1], plus a boundary-acceptance probe at 0 + 1 for the inclusive ends) and `trading_system/backtesting/config.py` 64% → 100% (BacktestConfig.__post_init__: start ≥ end, zero/negative starting_capital, negative spread_pct + the zero-boundary acceptance, injection currency mismatch, injection before start, injection after end, plus the inclusive start/end-boundary acceptance probes; frozen-dataclass smoke). 21 new tests across `tests/structured_products/test_admission.py::TestDecompositionInvariants` (8 tests) + `tests/backtesting/test_config.py` (13 tests). C4 (operator-token rotation) was Proposed as CR-024 instead of implemented because the surface is non-trivial (revocation list + multi-secret roll + audit log + CLI) and warrants a full SRS / SDS / SDD / TP cascade — design captured in `Documentations/Change-Requests.md`. Full suite 2 555 → 2 576.
  - [x] **C7 — Docker container hardening (static portion)** ✅ DONE 2026-05-23 @ `<this commit>`. Adds non-bypassable container hardening across `Dockerfile` + `compose.yaml` + `.dockerignore`: `STOPSIGNAL SIGTERM` on the Dockerfile so uvicorn's clean-shutdown handlers (structured log flush, broker unsubscribe) actually run; compose-side `init: true` so tini reaps as PID 1 and forwards SIGTERM correctly; `read_only: true` root fs + `tmpfs: /tmp` for the writable scratch space SQLite WAL + uvicorn temp files need; `cap_drop: [ALL]` + `security_opt: no-new-privileges:true` (defence-in-depth on top of the existing `USER trading` non-root uid); `mem_limit: 1g` + `pids_limit: 256` against runaway backtest jobs / fork bombs; `stop_grace_period: 30s` matching the new STOPSIGNAL contract; structured-log env vars (TRADING_BOT_LOG_LEVEL / TRADING_BOT_LOG_HUMAN) wired into the service environment per the C2 contract. `.dockerignore` gains `config/` (belt-and-suspenders so a future `COPY . .` regression can't bake secrets into the image), `*.sqlite*`, `.env*`, `*.secret`, `*.key`. 15 new static tests in `tests/webapp/test_dockerfile.py` cover every contract: STOPSIGNAL declared; init: true; read_only true; cap_drop ALL; no-new-privileges:true; tmpfs /tmp with size cap; mem_limit + pids_limit + stop_grace_period declared; no docker.sock mount; no privileged: true; no ENV-baked TRADING_BOT_OPERATOR_SECRET in the Dockerfile; no literal-value secrets in compose.yaml (only ${VAR} interpolation); .dockerignore covers the secret-artefact patterns; config/ bind is `:ro`. Dynamic portion (CVE scan via trivy/grype + container runtime smoke against a live daemon) deferred — needs the Docker daemon in CI which isn't in this sprint's scope. Strengthens REQ_F_FAS_007 + REQ_NF_FAS_002. Full suite 2 540 → 2 555.
  - [x] **C6 — Operations.md v1.0 finalisation** ✅ DONE 2026-05-23 @ `<this commit>`. Bumps `Documentations/Operations.md` from v0.1 draft to v1.0 reflecting every shipped CR through CR-022. Header status flipped to v1.0 + maintenance footer rewritten to point at the standard CR cascade for amendments. §10.2 (Feed lag) updated: obsolete `tools/yfinance_recorder.py --symbols` command replaced with the CR-009/CR-021 multi-symbol path (`tools/yfinance_recorder_universe.py --universe cac40 --start ... --sleep-seconds 2.0 --retry-on-rate-limit 3`); references the actual `var/yfinance-cache/` cache root + the CR-022 live-bypass behaviour. §10.5 (Notifications): WhatsApp → Slack per CR-018; `TRADING_BOT_SLACK_WEBHOOK_URL` env var documented. §7 + §8 gain "regression guardrails" callouts pointing at the C8 + C5 drills shipped earlier in this sprint. Two new sections: **§14 — Structured logging + correlation IDs** (`TRADING_BOT_LOG_LEVEL` / `TRADING_BOT_LOG_HUMAN` env vars; JSON-line schema; X-Request-ID round-trip example; account_id binding via path patterns; triage workflow with `jq` filters) — anchors REQ_NF_LOG_001 + REQ_SDS_CRS_001 from C2. **§15 — Paper-trading lifecycle** (start via wizard; backfill / live phases; cached-only banner; stop / resume; common-issue table; hard reset) — anchors REQ_F_PAP_001..005 + REQ_F_PAP_010 from CR-019 + CR-022. §13 references expanded to cite CR-018 / CR-019 / CR-020 / CR-021 / CR-022 + the three Phase-8 drill test files. Quick-reference table grows two new rows for §14 and §15. 921 lines total (was 706). No new tests; pure docs slice.
  - [x] **C8 — Multi-account drill** ✅ DONE 2026-05-23 @ `<this commit>`. Ships `tests/integration/test_multi_account_drill.py` (21 tests) exercising the CR-006 multi-account surface end-to-end with a 3-account household (alpha 1 k EUR, beta 5 k EUR, gamma 20 k EUR; total 26 k EUR). Registry: lex-by-id ordering, duplicate-id rejection (`accounts:duplicate_id:<id>`), deterministic tick fan-out (two ticks with same `(now, pipeline)` produce identical observed-account sequences), `is_single_account()` short-circuit false on three. PortfolioGroup: household_equity sums to 26 000 EUR, exposure_by_instrument aggregates ASML (3 k EUR across all three accounts) + AIR.PA (1.5 k EUR in gamma), FX-missing surfaces categorised `accounts:fx_missing:USD:EUR` Err. Cross-account concentration gate: single-account no-op (REQ_NF_ACC_001), passes under cap, rejects when projected household share crosses cap with categorised `risk:cross_account_concentration:<instrument>` Err, SELL reduces projected share, currency-mismatched exposures surface distinct Err, zero/negative household equity + bad cap_pct rejected. HouseholdDrawdownTrigger: degrade/kill thresholds (12% / 15%) emit appropriate `KillSwitchTrigger` rows; KILL pre-empts DEGRADE on the same tick; max-across-accounts aggregation (REQ_SDD_ACC_006) — one account at 20% drawdown triggers KILL even when the other two are clean; inverted threshold rejected at construction. End-to-end: deterministic tick + household snapshot pair; gate consumes live `PortfolioGroup` aggregations (proves the production wiring path). Strengthens evidence on REQ_F_ACC_001..010, REQ_NF_ACC_001, REQ_SDS_ACC_002 / 004, REQ_SDD_ACC_001 / 002 / 004 / 005 / 006 — no new REQs (drill exercises existing requirements under realistic 3-account load). Full suite 2 519 → 2 540.
  - [x] **C5 — Persistence migration drill** ✅ DONE 2026-05-23 @ `<this commit>`. Ships `tests/persistence/test_migration_drill.py` (16 tests) exercising the bundled migration pipeline harder than the existing TC_PER_002..004 covered: full pipeline (0001..0006) applies on a fresh DB + every documented table present (`equity_points`, `transitions`, `approval_requests`, `hypotheses`, `idempotency_entries`, `backtest_jobs`, …); idempotency on second run; dry-run leaks no tables beyond `schema_migrations`; SHA-lock rejects retroactive edits to ANY shipped migration (parametrized over 0001 / 0003 / 0006); **WAL recovery from a simulated crash** — open `BEGIN IMMEDIATE`, write an uncommitted row, drop the raw fd without COMMIT/ROLLBACK, re-open over the same path → committed rows survive + uncommitted row rolled back (REQ_SDS_PER_004); cross-restart durability via repository → close → reopen → re-read; new migration applied on a populated DB leaves pre-existing rows intact; `journal_mode=WAL` + `foreign_keys=1` + `busy_timeout` PRAGMAs match construction; no `*down*.sql` scripts shipped (forward-only invariant); AST audit that `import sqlite3` lives only under `trading_system/persistence/` (REQ_F_PER_010 boundary); malformed-SQL migration aborts the run atomically (later migrations stay un-applied; `schema_migrations` records only the cleanly-applied set); clean open + migrate emits no `sqlite3.Warning`. Strengthens evidence on REQ_F_PER_001 / 003 / 004 / 010, REQ_NF_PER_001, REQ_SDS_PER_003 / 004, REQ_SDD_PER_001 / 004 without adding new REQs. Full suite 2 503 → 2 519.
- **CR-021 — Range-aware YFinanceCache lookup** ✅ DONE 2026-05-23 @ `<this commit>` — Accepted + full lifecycle cascade landed. Adds REQ_SDD_DAT_014 (two-pass lookup: exact-key first; envelope scan on miss; widest enveloping cached file wins; bars sliced to `[key.start, key.end]`) + REQ_NF_DAT_004 (slice byte-equal to an exact-key recorder run). Amends REQ_F_DAT_005 in-place per REQ_NF_LIF_002. `_parse_filename_window` helper inverts `CacheKey.filename()` for both new and legacy (naïve) timestamp encodings; cached `Bar.at` normalised to UTC on read so the envelope predicate compares uniformly. Side-effect speedup: `_scan_latest_cached_bar` now picks the newest-end file by filename metadata and reads one file (instead of parsing every jsonl under the symbol); `YFinanceMarketDataProvider.latest()` memoises per-symbol in offline mode. Full CAC 40 / 17-month backtest: ~15 s (was timing out at 300 s) — ~20× faster. Quick fix shipped alongside: webapp jobs-form prefill now defaults to `2025-01-01..2026-05-23 / cac40` matching the operator's recorded cache; `tests/webapp/test_worker_attribution.py` made self-contained against the bundled `eu-dividend-starter` fixtures so it survives the operator-tuned `config/system.yaml`. 5 new tests at `tests/data/yfinance/test_cache.py::TestRangeAwareLookup`. SRS / SDS / SDD / Test-Plan re-approval rows all stamped 2026-05-23.
- **CR-022 — Live-bypass fetch for paper-trading polls** ✅ DONE 2026-05-23 @ `<this commit>` — Accepted + full lifecycle cascade landed. Adds REQ_F_PAP_010 (post-backfill paper bar source SHALL force-refresh upstream per poll; falls back to the CR-021 envelope cache on network failure or `allow_network=False`) + REQ_SDD_DAT_015 (`YFinanceMarketDataProvider.fetch_live_bars(...)` bypass-cache contract). Motivation: after CR-021 landed, the range-aware cache satisfied every paper-trading poll from disk, pinning the dashboard to stale bars (Laurent: "paper trading just uses the cache, no live data feed"). CR-022 decouples the two poll modes — backtest replay stays cache-first (REQ_NF_DAT_001 untouched); paper polling forces the network. The bar source duck-types onto the new method via `getattr` so legacy test fakes / simulated sources keep working. 3 new provider tests + 3 new bar-source tests. SRS / SDS / SDD / Test-Plan re-approval rows all stamped 2026-05-23. Full suite 2 499 passed.
- **CR-003 — News-feed secondary signal** (Deferred 2026-05-16 @ wiki 283041f). Re-triage after CR-007 + CR-012 cascades land; orthogonal to portfolio mechanics. No REQ ids reserved.
- **CR-020 — Plotly + Kaleido as the report-rendering stack (drop matplotlib)** (**Accepted 2026-05-22; design cascade complete + code landed @ `<this commit>`** — SRS §3.29 amended, SDS §3.34 amended, SDD §11.18 + new `REQ_SDD_RPT_004` in §13.28, Test-Plan §3.15n TC_RPT_005..009 amended + new TC_RPT_010 for the CR-019 compare-two-runs helper). Rewrote `trading_system/analytics/equity_chart.py` end-to-end: `render_equity_html(curve)` returns a self-contained interactive Plotly page with the JS bundle INLINED via `include_plotlyjs="inline"` (no CDN, modebar disabled so the rendered DOM never inserts an `<a href="plotly.com">` logo); `render_equity_png(curve)` calls Kaleido's static-export backend at fixed 1000 × 400 px @ scale=1 for replay-deterministic PNG bytes; new `render_equity_comparison_html(curves)` helper overlays two equity curves on a single figure with the legend toggle — consumed by the CR-019 backtest "compare two runs" panel (REQ_F_WEB2_005 lands at TEST as a side-effect). Renderer signature change: `render_equity_html` now takes `curve` directly (was `png_bytes` pre-CR-020); `report.py`'s call site rewired accordingly. `pyproject.toml` `[reports]` extra: `matplotlib>=3.7` → `plotly>=5.20` + `kaleido>=1.0.0` (the v0.2 line was deprecated in Sept 2025 and emits a noisy `DeprecationWarning` on every `to_image()`; v1.0+ keeps the same `Figure.to_image(format="png", ...)` API and now ships an in-process Chromium via the `choreographer` runtime). Test updates: `tests/analytics/test_report.py` + `tests/test_cli.py` swap `importorskip("matplotlib")` → `importorskip("plotly")` + `importorskip("kaleido")`; the base64-PNG round-trip test (`test_equity_curve_html_embeds_png_base64`) becomes `test_equity_curve_html_uses_inline_plotly_bundle` (asserts `Plotly.newPlot` reach + inline `<script>` tags); the no-network test (`test_equity_curve_html_has_no_external_resource_refs`) strips inline `<script>` bodies before scanning for CDN hrefs so Plotly's own source-code string literals (modebar Logo `plotly.com` reach — dead code) don't trip the audit; the import-graph audit additionally rejects any `matplotlib` reach (matplotlib was retired). `var/reports/2024-baseline/manifest.json` regenerated — `png_sha256` baseline changes (Kaleido-rendered PNG bytes differ from matplotlib's). 51 analytics + CLI tests pass; full webapp suite 175 pass; conformance suite 35 pass. `REQ_F_RPT_001`, `REQ_NF_RPT_001`, `REQ_SDD_RPT_004`, `REQ_F_WEB2_005` at TEST.
- **CR-019 step 1 (b) ✅ DONE 2026-05-23 @ `<this commit>` — 543 / 543 REQs at TEST (100%).** Operator-grade webapp shipped end-to-end across thirteen slices: paper-trading runtime + persistence wiring + dashboard panel + onboarding wizard + session stop + simulated bar source + tick driver + strategy/risk-gate/broker.submit wiring + mode switch + backtest workflow polish + reports panel + notifications inbox + accessibility audits + recovery wizard + strategy registry + multi-account switcher with household-drawdown indicator + fragment query-param pattern. Step 2 (live trading) remains a separate amendment gated on REQ_F_BRK_003. CR-019 step 1 documentation index follows below — each row is the original slice description.

- **CR-019 — Operator-grade webapp (paper trading + backtest + future live trading)** (**Accepted 2026-05-22; design cascade complete @ wiki `<this commit>`** — SRS §3.33, SDS §3.38–§3.39 + §8.24, SDD §11.20 + §13.30, Test-Plan §3.15p). 34 new REQs at TP: `REQ_F_WEB2_001..010` (operator UX surface — onboarding wizard, mode switch, paper panel, backtest workflow, reports panel, registry, recovery wizard, multi-account switcher, notifications inbox, expanded live-state), `REQ_NF_WEB2_001..005` (usability + accessibility — no SPA, WCAG-AA, keyboard nav, reduced-motion, aria-labels), `REQ_F_PAP_001..005` (paper-trading runtime — wraps LocalBrokerAdapter + CR-009 yfinance; cached-only graceful degradation; session resume; `paper-*` account_id namespace; one live-ticking session per account_id), `REQ_SDS_WEB2_001..004` (SDS-level design — module layout, fragment Protocol, accessibility surface, paper-runtime types), `REQ_SDD_WEB2_001..010` (SDD-level rules — HTMX fragment Protocol, wizard state cookie, runtime classes, degradation path, resume, contrast audit, focus trap, reduced-motion swap, aria-label discipline, inbox ring buffer). 18 new TCs in §3.15p (TC_WEB2_001..015 functional + accessibility, TC_PAP_001..006 paper runtime, TC_ACC_WEB2_001..002 multi-account, TC_KS_WEB2_001..002 recovery wizard). Step 2 (live trading) remains a separate amendment gated on REQ_F_BRK_003. Side-effect — `tools/traceability-report.py` regex updated from `[A-Z]+` to `[A-Z][A-Z0-9]*` per segment so `WEB2`-style family ids parse cleanly (538 → 542 REQs tracked). Suggested implementation staging: (a) onboarding + paper-trading runtime, (b) dashboard panels + backtest workflow, (c) strategy registry + operator controls + multi-account, (d) accessibility audit + final polish. Phase 4 → 5 gate reopens for the CR-019 cascade only. **Step 1 (b — simulated bars + tick driver + session switcher) ✅ DONE 2026-05-22 @ `<this commit>`** — closes the wizard → dashboard loop so the operator can validate the whole flow in the browser. New `trading_system/webapp/runtimes/simulated_bar_source.py` ships `SimulatedBarSource` — a deterministic Gaussian-random-walk bar generator (REQ_F_PAP_001 BarSource Protocol satisfied). Each `next_bar()` advances the internal clock by `step_seconds` (default 60s of "market time") and emits a bar whose close drifts by `vol_bps * z_score / 10000` from the previous close. RNG is seeded from the caller-supplied `seed`; same seed + same call sequence ⇒ identical bar stream (REQ_NF_DET_001). `latest_cached()` mirrors the most recent bar for the runtime's degradation-fallback path even though this source never errs. New `trading_system/webapp/runtimes/tick_driver.py` ships `PaperTickDriver` — an asyncio loop that sweeps every registered paper runtime per `interval_seconds` (default 2s) and invokes `tick_once()` on the live ones. A failing `tick_once` (e.g., persistence Err) is logged but SHALL NOT halt the loop — one bad runtime doesn't pull down the others. `start()` is idempotent; `stop()` cancels the task + awaits its exit. Wired into the FastAPI lifespan in `app.py` — starts on first request, stops on shutdown. The wizard's finish handler in `routers/views/onboarding.py` now actually builds a `PaperTradingRuntime` + registers it: hardcoded `_DEFAULT_INSTRUMENTS` per universe (`ASML.AS` for eu-dividend-starter, `AIR.PA` for cac40), Phase-1 `PhaseConstraints`, `SimulatedBarSource` seeded from the account_id hash, no-op strategy stub (real strategy wiring lands in a follow-up — the runtime's strategy step is skipped when `market_data_provider=None`). Failures bubble back to the wizard's banner; success registers in the shared `app.state.runtime_registry` so the dashboard panel sees it immediately. Session-recovery polish: the wizard's finish sets a 1h `active-paper-session` cookie + the dashboard view falls back to it when no `account_id` query param is provided (so a refresh of `/` after closing the tab still shows the session); the dashboard template gains a session-switcher `<select>` populated from `registry.live_account_ids()` for operators with multiple paper sessions. Base nav grows a "New paper session" link to `/onboarding`. 11 new tests at `tests/webapp/test_simulated_bar_source.py` (deterministic bar stream, distinct seeds diverge, cached-bar sentinels, bad-config rejection, clock advance) + 4 PaperTickDriver tests (bad interval, runs a runtime tick, stop is idempotent, continues after a tick Err) + 1 e2e onboarding test (wizard → finish → GET /api/accounts/<aid>/paper-state reports is_alive=true). New tests carry the `@pytest.mark.wallclock` marker per REQ_TP_FIX_001 since the tick driver loop uses `asyncio.sleep`. Full webapp + conformance suite 201 pass. REQs at TEST: REQ_F_PAP_001 (already lifted in step 1 (a)) confirmed end-to-end, REQ_F_PAP_002 caching path exercised.

**Step 1 (b — onboarding wizard) ✅ DONE 2026-05-22 @ `<this commit>`** — REQ_F_WEB2_001 + REQ_SDD_WEB2_002 close at TEST. 3-step server-rendered HTMX-friendly wizard: GET `/onboarding` (capital + universe) → POST `/onboarding/step2` (validate + advance) → POST `/onboarding/step3` (strategy selection) → POST `/onboarding/finish` (create `PaperTradingSession` identity card under fresh `paper-<utc-iso-timestamp>` account_id + 303 redirect to `/?account_id=…` with the wizard cookie cleared + a short-lived `paper-session-created` breadcrumb cookie for the dashboard toast) / POST `/onboarding/cancel` (clear cookie + 303 → `/`). New `trading_system/webapp/wizard_state.py` ships the HMAC-signed cookie helpers (`encode_state` / `decode_state` / `is_valid_capital`) — the cookie format is `<base64url-canonical-json>.<hex-signature>` signed via the existing `AccountScopedTokenVerifier.secret`. The cookie is `httponly`, `SameSite=Lax`, `max_age=3600`. Both `ALLOWED_UNIVERSES = ("eu-dividend-starter", "cac40")` and `ALLOWED_STRATEGIES = ("CoreStrategy", "TacticalStrategy")` are closed sets — adding a new entry is a deliberate code change + wiki amendment. New `trading_system/webapp/routers/views/onboarding.py` ships the 5 handlers; new `trading_system/webapp/templates/onboarding.html` ships the 3-step form with `role="dialog"` + `aria-modal="true"` + `aria-labelledby` on the outer container, banner-style server-side error messages with `role="alert"` + `aria-live="polite"`, a `<ol class="wizard-steps">` progress indicator. The runtime ticking itself is deferred — the finish handler creates the session *identity* (universe + strategy + capital choices); the BarSource (yfinance adapter wiring) + actual `PaperTradingRuntime` registration lands in the next slice. `WebappState` gains a `runtime_registry` slot lifted onto `app.state.runtime_registry` so the wizard's finish handler + the paper-state reader share the same registry instance. 15 new tests at `tests/webapp/test_onboarding.py` cover: cookie encode/decode round-trip + replay determinism + tampered-signature rejection + bad-b64 rejection + unknown-universe rejection + closed-set invariant; route handlers (GET step 1 renders the dialog with the modal attributes; POST step 2 with valid form advances + sets the signed cookie; POST step 2 with bad capital returns 400 + banner; POST step 2 with unknown universe returns 400; POST step 3 advances to confirm carrying choices forward; POST step 3 with unknown strategy returns 400; POST finish redirects + clears wizard cookie + sets paper-session-created breadcrumb + returns a fresh paper- account_id in the redirect URL; POST cancel redirects to / + clears cookie). OpenAPI snapshot regenerated (added 5 new paths under `/onboarding`). Full suite 2259 → 2274.

**Step 1 (b — dashboard panel + SSE) ✅ DONE 2026-05-22 @ `<this commit>`** — REQ_F_WEB2_003 closes at TEST. New canonical response schema `PaperStateResponse` (frozen dataclass; 8 fields: account_id / as_of / is_alive / is_degraded / degraded_since / last_tick_at / equity_points_count / latest_equity_after_tax — Decimal-as-TEXT + ISO-8601 datetimes per the byte-identical-replay contract). New `trading_system/webapp/paper_state_reader.py` module: `PaperRuntimeView` + `PaperRegistryView` Protocols + `RuntimePaperStateReader` concrete reader (paper_state(account_id, as_of) request-response method + subscribe(account_id) async iterator at 2s default cadence). New router `routers/api/paper_state.py` mounts `GET /api/accounts/{account_id}/paper-state` (Bearer auth via `RequestRequireAnyValidClaim`, canonical-JSON body). SSE router `sse.py` extends to `GET /events/paper-state?account_id=<id>` — emits one `event: paper-state` per reader tick with the canonical-JSON snapshot as `data:`; SSE event id is the snapshot's `as_of` ISO-8601 timestamp so `hx-sse` resumes after disconnect. `app.py` gains a `paper_state_reader` slot on `WebappState` + wires a `_default_paper_state_reader()` over an empty `RuntimeRegistry` so a fresh container's dashboard shows the documented "session not registered" all-zeroed shape (placeholder until the onboarding wizard lands). Dashboard template gains a new `<section>` panel with: status badge (Live / Stopped / No session), degraded banner (REQ_F_PAP_002), equity-after-tax stat, ticks-recorded stat, last-tick + degraded-since timestamps. HTMX `hx-ext="sse"` + `sse-connect` polling at the new SSE endpoint; client-side `renderPaperState()` extracts fields from the JSON sink. 9 new tests at `tests/webapp/test_paper_state_routes.py`: reader unit tests (no-session sentinel, live-runtime mapping, tick-seconds invariant), request-response route tests (auth gate, no-session payload, live payload, byte-identical replay on pinned clock), SSE channel tests (auth gate + async subscribe iterator). OpenAPI snapshot regenerated (added 2 new paths: `/api/accounts/{account_id}/paper-state` GET + `/events/paper-state` GET). Full suite 2250 → 2259. The SSE TestClient round-trip stays out of test coverage — `httpx.iter_bytes()` against an infinite-loop SSE generator deadlocks in the synchronous TestClient — and we cover the async-iterator surface directly instead.

**Step 1 (b — persistence wiring) ✅ DONE 2026-05-22 @ `<this commit>`** — REQ_F_PAP_003 closes at TEST. `PortfolioRepository.list_account_ids_with_prefix(prefix)` added — single indexed `SELECT DISTINCT account_id FROM equity_points WHERE account_id LIKE ?` query, returns `tuple[AccountId, ...]` ordered ASC; rejects empty prefix with `persistence:bad_prefix:empty`. `PaperTradingRuntime` gains an optional `equity_repo: PortfolioRepository | None` slot — when wired, every successful `tick_once` ALSO persists the just-recorded equity point via `append_equity_point(point, account_id=session.account_id)`; persistence failures surface as `Err("paper:persist_equity_point:<repo-err>")` so the dashboard can show a "saving disabled" banner without crashing the live session. `RuntimeRegistry.resume_from_persistence(repo) -> Result[tuple[AccountId, ...], str]` shifts from a v1 stub returning 0 to a real discovery call — returns every `paper-*` account_id with at least one persisted equity_point row. v1 surface is **discovery only**: the registry does NOT auto-revive live ticking because session metadata (universe / strategy_id / instrument) isn't persisted yet — an operator picks one of the returned ids from the recovery wizard and re-supplies the missing inputs. 5 new tests at `tests/webapp/test_paper_trading_runtime.py`: persists-when-wired happy path, Err-mapping when the repo fails, prefix filter ordering, empty-prefix rejection, registry resume discovery + empty-DB case. Full suite 2247 → 2250.

**Step 1 (a) ✅ DONE 2026-05-22 @ `<this commit>`** — paper-trading runtime core. New `trading_system/webapp/runtimes/paper_trading.py` ships `PaperTradingSession` + `PaperTradingRuntime` + `RuntimeRegistry` + `new_paper_account_id` + `build_runtime`. `tick_once()` is the unit of work; graceful degradation per REQ_F_PAP_002 falls back to `BarSource.latest_cached()` on `data:upstream_blocked` / `network:timeout` Errs and surfaces `is_degraded()` + `degraded_since()` for the dashboard banner. The CR-017 structural audit (`tests/webapp/test_structural.py`) gained a documented carve-out: `webapp/runtimes/` MAY import `execution.*` / `backtesting.*` / `data.*` / `portfolio.*` / `tax.*` / `strategies.*` (the composition layer); `safety` / `risk` / `strategy_lab` stay forbidden even in the runtime layer. 18 new tests at `tests/webapp/test_paper_trading_runtime.py`. REQs reaching TEST: REQ_F_PAP_001 / 002 / 004 / 005, REQ_SDS_WEB2_004, REQ_SDD_WEB2_003 / 004. `REQ_F_PAP_003` (session resume via CR-008 persistence) stays at CODE — `RuntimeRegistry.resume_from_persistence` is a v1 stub returning 0; the persistence wiring is the deferred slice. v1 deliberately also defers strategy + risk-engine wiring INSIDE `tick_once` — proposals are observed but not executed yet; equity snapshots record so the dashboard renders. Step 1 (b) lands those follow-ups.
- [x] `main.py` — runnable demo: connect (mock or selected broker adapter) → screener → trades → phase logic → portfolio sim → after-tax results; reads starting capital from config ✅ DONE 2026-05-08 (trading_system/main.py: loads config/system.yaml + config/phases.yaml + config/risk.yaml; builds a 3-stock EU dividend universe (ASML/BNP/SAN) with hand-registered fundamentals on the deterministic mock provider; runs screener → CoreStrategy → Backtest → Dashboard; prints after-tax summary. CLI: `python -m trading_system.main --start ... --end ... [--with-slippage]`. Smoke test in tests/test_main.py covers the success path, the err path on missing config dir, and the slippage branch. REQ_O_001..003 reach TEST.)

---

## Phase 6 — Test Execution

- [x] **Conformance suite** — `tests/conformance/` ✅ DONE 2026-05-21 @ `<this commit>`. Four AST-driven audit files closing 25 TP REQs in one slice (48 → 23 TP). `test_naming.py` (REQ_SDD_NAM_001/002/003 — Ruff `N` selector check + concrete-Adapter audit + Config-suffix audit with grandfathered allow-list); `test_imports.py` (REQ_SDD_IMP_001/002/003/004 + REQ_SDS_FLO_004 — every required top-level package exists with `__all__` and a REQ-id-referencing docstring; package import graph acyclic; strategy_lab off the runtime path except `registry`); `test_traceability_meta.py` (REQ_TP_COV_002/003 + REQ_TP_INT_001 — `traceability-report.py --check` exits 0; every approved REQ named in the Test-Plan modulo 6 grandfathered transitive entries); `test_clock_discipline.py` (REQ_TP_FIX_001 — every test using `time.sleep`/`asyncio.sleep` marked `@pytest.mark.wallclock`); `test_behavioral_and_safety.py` (REQ_C_BHV_004/005 + REQ_S_KS_004/006/012 + REQ_SDS_ARC_003/004 + REQ_SDS_CRS_004 + REQ_F_CAP_001 + REQ_F_TAX_005 — kill-switch surface present, no `all_in`/`yolo`-style forbidden helpers, RiskConfig per-trade cap, AnomalyAlert payload reachable, `main.py` single-process, starting capital from config not hardcoded, backtesting imports `tax/`). The audits are structural / file-walk only; the heavier REQs (REQ_TP_STR_002 hypothesis property tests, REQ_TP_STR_003 BDD scenarios, REQ_TP_GAT_001/002 perf benchmarks, REQ_SDD_TST_004/005/006 fixture invariants, REQ_TP_STR_004 broker conformance suite) remain TP and need real test files. Side-effect: refactored `TransitionEvent` from `regime/transition.py` to `models/phase.py` so the `persistence ↔ regime` package cycle goes away (REQ_SDD_IMP_003 acyclic-graph audit was the forcing function); `regime/transition.py` re-exports it for backwards compat. `tests/webapp/test_job_queue.py` gained `pytestmark = pytest.mark.wallclock` (the file uses real `time.sleep` on the ProcessPoolExecutor). 22 new conformance tests; full suite 2012 passed.
- [x] **Run unit suite** ✅ DONE 2026-05-22 @ `<this commit>` — full suite 2 224 passed (default `pytest -q --ignore=tests/benchmark`). Coverage **89 %** overall (10 678 statements, 3 418 branches; 111 files at 100 %; ~135 of 187 files ≥ 90 %). 25 financial-logic files sit below the 90 % gate (see `Documentations/Validation.md` §2.2) — mostly YAML loaders (deep cross-product on malformed-YAML branches deferred) + Protocol stubs (`@runtime_checkable` `...` bodies are flagged uncovered though no production path executes them). Effective coverage on the load-bearing financial-logic core sits ≥ 95 %. Coverage snapshot at `docs/coverage_report.txt`.
- [x] **Run integration suite** ✅ DONE 2026-05-22 @ `<this commit>` — 92 integration drills under `tests/integration/` pass: kill-switch (10), Phase-5 tax-harvest (7) + FX (11), walk-forward (6), Phase-6 (13), edge cases (17), broker conformance (17), SP stress (14) + existing `test_phase5_stack.py` smoke. Memory-resident fixture stacks, no wall-clock dependencies; deterministic replay throughout (REQ_NF_REP_001).
- [x] **Run backtests on historical data** ✅ DONE 2026-05-22 @ `<this commit>` — both shipped strategies (`CoreStrategy` + `TacticalStrategy`) exercise the seeded `MockMarketDataProvider` across walk-forward windows (BULL / BEAR / SIDEWAYS regime crossings inherent in the random-walk bars). CR-009 yfinance adapter is wired through `tools/yfinance_recorder.py` for operator-driven multi-year recording; v1 shipped fixture covers 2024 weekday closes for 3 EU dividend stocks at `data/yfinance_fixtures/`. Multi-regime crossings on the full Phase-5+ 7-year window remain a Phase-8 hardening item (`Validation.md` §5).
- [x] **Walk-forward validation on every shipped strategy** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_walk_forward_drill.py` (6 scenarios). Drives the `walk_forward` orchestrator against both shipped strategies (`CoreStrategy` + `TacticalStrategy`) on the seeded `MockMarketDataProvider`, with a compressed (30d/15d/15d) window across a 9-month period producing ~6 step results per strategy. Asserts: (1) orchestrator returns `Ok`, (2) ≥ 1 `WindowResult` generated, (3) each window carries finite `Decimal` Sharpe ratios for train/valid/oos, (4) `WFResult.collapsed` is False — no shipped strategy trips the 0.5× train-Sharpe collapse detector on benign random-walk bars, (5) replay determinism — two runs with the same seed produce byte-equal equity curves + Sharpe values, (6) too-short period returns `Ok(WFResult(windows=(), collapsed=False))` (vacuously False, not an Err), (7) invalid period returns categorised `walk_forward:invalid_period:...` Err. The extended-window default (60m/12m/24m for phase 5+) is asserted via a small smoke test against the `WalkForwardWindow.phase5_plus()` classmethod ⇒ total ≥ 7 years for multi-regime crossings (REQ_SDD_ALG_004). REQs reaffirmed: REQ_F_STR_003, REQ_F_BCT_008, REQ_F_BCT_009, REQ_SDD_ALG_004, REQ_NF_REP_001, REQ_TP_GAT_003.
- [x] **Phase-5 drills** ✅ DONE 2026-05-22 @ `<this commit>` — two integration drills under `tests/integration/`. **Tax-loss harvest** (`test_phase5_tax_harvest_drill.py`, 7 scenarios): builds a mixed-PnL realization ledger spanning two fiscal years, runs `harvest_losses` against accumulated gains, asserts greedy largest-loss-first selection, fiscal-year discipline (prior-year losses ignored), after-tax improvement (post-harvest taxable base ≤ 0 ⇒ no tax owed where 360 EUR was previously due), cross-currency programmer-error panic, and the non-negative-magnitude HarvestSuggestion shape. **FX hedge P&L attribution** (`test_phase5_fx_hedge_drill.py`, 11 scenarios): builds a multi-currency portfolio (EUR base + USD + CHF + GBP), drives one full hedge cycle per currency (compute_fx_exposure → FXHedger.propose_hedges → ledger.open → FX-rate movement → ledger.close), asserts the mark-to-market formula `notional × (exit/entry - 1)` per-forward, sums to `realized_pnl_gross`, and applies REQ_F_FXH_006 tax treatment (gains × 0.70; losses pass through). The full-cycle attribution test verifies USD +1000 EUR / CHF -288 EUR / GBP 0 EUR = +712 EUR gross → +498.40 EUR after-tax, with each forward's mark hand-computed and asserted byte-exact. REQs reaffirmed: REQ_F_TAX_004/006, REQ_F_FXH_002/003/005/006, REQ_NF_FXH_001, REQ_SDS_FXH_001/002, REQ_SDD_FXH_004.
- [x] **Phase-6 drills** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_phase6_drills.py` (13 scenarios) exercises `EnsembleStrategy`'s three Phase-6 properties: (1) **vol-target tracking** — 5 scenarios pinning the scaler math: neutral at portfolio_vol == target_vol, halved at 2×, doubled at 0.5×, clamped at 1.0 for extreme over-scaling, neutral on zero/negative portfolio_vol; (2) **risk-parity weight stability** — 4 scenarios: weights ∝ inverse-vol (10/15 + 5/15 for vols [0.10, 0.20]); ≤ 10% relative weight change under a +5% perturbation of any member's vol (no catastrophic flip); weights sum to 1 within 1e-15 across 5 members; weights deterministic across recompute; (3) **ensemble decorrelation** — 3 scenarios: low-vol member gets the largest weight; risk-parity theoretical portfolio variance (Σ wᵢ² σᵢ² under uncorrelated members) is strictly less than equal-weight variance for disparate vols (~58% reduction for vols [0.05, 0.10, 0.20]); combined proposal sizes sum to `base × scaler` (weights sum to 1 by construction); plus a deterministic-replay smoke. REQs reaffirmed: REQ_F_STR_004, REQ_SDD_ALG_010, REQ_NF_REP_001.
- [x] **Edge-case tests** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_edge_cases_drill.py` (17 scenarios) walks the four documented failure modes: (1) **crash drill** — single-day-loss + rapid-decline anomaly detectors fire at documented thresholds (5% / 10% over 5 days), operator-escalated KILL trigger halts trading; (2) **knockout drill** — LONG turbo at strike 90 triggers when underlying touches 88 (closes at zero, loss capped at cost basis); SHORT turbo at 110 triggers on 112; below-barrier tick does NOT trigger; (3) **broker rejection drill** — `broker:no_market_data`, `broker:order_unsupported`, `broker:not_found`, `broker:already_filled` Errs surface correctly; resubmit-same-id is idempotent (REQ_SDD_API_006); (4) **feed corruption drill** — Bar/Tick/Order constructors panic at the boundary on invalid shapes (high<low, negative price, bid>ask, last outside spread, non-positive quantity). REQs reaffirmed: REQ_S_KS_003, REQ_SDD_ALG_006/007, REQ_F_BCT_004, REQ_F_TRB_005/006, REQ_F_BRK_001, REQ_SDD_API_006, REQ_SDD_DAT_001 family.
- [x] **Broker-adapter conformance tests** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_broker_conformance_drill.py` (17 scenarios) is the explicit conformance gate for REQ_TP_STR_004. Runs the full BrokerAdapter Protocol surface against `LocalBrokerAdapter`: Protocol satisfaction (`isinstance(adapter, BrokerAdapter)` via runtime_checkable), every method reachable (`submit / cancel / positions / account_state / instrument / subscribe`), submit Ok on fill + idempotent on duplicate client id (REQ_SDD_API_006) + categorised Errs for `no_market_data` / `order_unsupported`, cancel `already_filled` / `not_found`, positions aggregate per instrument, account_state.cash equals starting_cash at boot + equity-identity (cash + cost_basis + unrealized) after fill, instrument lookup returns Some/Nothing, subscribe + cancel + idempotent re-cancel, full buy-sell round-trip produces positive realized P&L and zero open positions. Future live-broker adapters MAY ship only after passing this same suite — parametrize the `adapter` fixture to swap in any concrete impl. REQs reaffirmed: REQ_F_BRK_001..005, REQ_SDS_INT_001, REQ_SDD_API_002, REQ_SDD_API_006, REQ_TP_STR_004.
- [x] **Kill switch trip/recovery drill** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_kill_switch_drill.py` (10 scenarios). Boots a full safety stack (StateManager + MemorySnapshotSink + MemoryAlertChannel + NotificationFanOut + MemoryNotificationChannel), then walks ACTIVE → DEGRADED → KILL → recovery-rejected → recovery-accepted → KILL → recovery in one linear scenario plus 9 narrower scenarios. Asserts: (1) state timeline matches the documented sequence, (2) `must_halt()` flips True at KILL and False after recovery, (3) every transition produces one non-empty `AuditSnapshot` (REQ_NF_AUD_001 family), (4) every transition fires one `KillSwitchEvent` through the `NotificationFanOut` bridge (REQ_F_NOT_003 / REQ_SDD_NOT_002), (5) rejected recovery surfaces the categorised Err (`safety:recovery_conditions_unmet` / `safety:invalid_operator_token`) and DOES NOT produce a snapshot, (6) idempotent same-state DEGRADE / KILL triggers still record an audit row (operator can replay). Covers every TriggerCategory (FINANCIAL / STRATEGY / EXECUTION / INTEGRITY) on the path. Six-snapshot expected count for the full lifecycle is documented inline.
- [x] **Structured product stress + liquidity drill** ✅ DONE 2026-05-22 @ `<this commit>` — `tests/integration/test_structured_product_stress_drill.py` (14 scenarios) walks the full admission flow + the three stress scenarios per REQ_F_STP_001..007. Happy path: BULL regime + 3% allocation + fresh portfolio = Ok(Decomposition) with four bounded fields. Gate-by-gate: BEAR/HIGH_VOL regimes return `regime_forbidden:<regime>` (REQ_F_STP_003/004); empty decomposer registry returns `not_decomposable:no_decomposer:...` (REQ_F_STP_002 / REQ_SDS_MOD_008); >10% allocation returns `cap_breach:...` (REQ_F_STP_001); tightened 4% issuer cap + 5% proposal returns `issuer_concentration:...` (REQ_SDD_ALG_014); negative or >100% allocation returns `data:bad_allocation_pct:...`; SP on underlying with existing turbo returns `stack_with_turbo:<underlying>` (REQ_F_STP_007). Stress scenarios: safely-bounded decomposition (worst_case_loss 0.20, no leverage) passes every shock; 2x-leveraged decomposition with worst_case_loss 0.30 fails the crash×leverage scenario (1×0.20×3=0.60 > 0.30); zero-equity-equiv cash-equivalent product passes vacuously. REQs reaffirmed: REQ_F_STP_001..007, REQ_SDS_MOD_008, REQ_SDD_ALG_012/013/014.

---

## Phase 7 — Validation & Traceability

- [x] **Final requirement traceability matrix** ✅ DONE 2026-05-22 @ `<this commit>` — 508 / 508 REQs at TEST (0 TP / 0 CODE). Generated by `tools/traceability-report.py` to `docs/traceability.csv` + `Documentations/Traceability.md`. `--check` mode is the CI gate (REQ_TP_COV_002).
- [x] **Coverage report** ✅ DONE 2026-05-22 @ `<this commit>` — 89 % overall (10 678 statements, 3 418 branches); 111 files at 100 %; effective ≥ 95 % on the load-bearing financial-logic core. Snapshot at `docs/coverage_report.txt`; analysis + below-gate list in `Documentations/Validation.md` §2.
- [x] **Known limitations** ✅ DONE 2026-05-22 @ `<this commit>` — captured in `Documentations/Validation.md` §5: broker selection deferred (only `LocalBrokerAdapter` shipped; conformance suite is the gate for any future live adapter); no live deployment until Phase 8 pre-flight; CR-003 news-feed signal deferred; Phase-5+ multi-year mock data drill deferred; hard-floor MC-gate (no per-phase / per-regime tuning); 25 financial-logic files below 90 % coverage (YAML loaders + Protocol stubs); no multi-account live runtime drill yet; CI workflow not yet wired.
- [x] **`Documentations/Operations.md` runbook** ✅ DONE 2026-05-22 — kill-switch operator runbook + manual recovery procedure already shipped (24.8 KB; 9 sections covering Quick reference, state machine, trigger taxonomy, recovery procedure, override gate, alerts, multi-account scoping, persistence recovery, snapshot recovery).
- [x] **Validation summary** ✅ DONE 2026-05-22 @ `<this commit>` — `Documentations/Validation.md` is the Phase-7 sign-off page: traceability summary, coverage table, test-execution count by tier, operational-drill table, known limitations, pre-deployment checklist, sign-off conditions. The DO-178C-inspired lifecycle is through Phase 7.

**Lifecycle rule:** any change after Phase 7 restarts the lifecycle from the affected phase.

---

## Phase 8 — Operator Hardening Sprint ✅ Closed (2026-05-26)

Post-Phase-7 hardening sprint targeting operator-grade production
readiness. Sprint board lives in
`Documentations/Feature-Gap-Analysis-2026-05-23.md` Part C
(14 open hardening items). Tracked separately from the lifecycle
because each item is operationally driven, not gated by an SRS
amendment.

The per-strike rows for C2 / C5 / C6 / C7-static / C8 / C1 (4
strikes) are kept inline under the Phase-5 entries (search for
"Phase-8 operator hardening sprint" in this file).

Sprint scoreboard at session close (2026-05-26):

- [x] **C2 — Structured logging + correlation IDs** ✅ Done.
- [x] **C5 — Persistence migration drill** ✅ Done (16 tests).
- [x] **C6 — Operations.md v1.0 finalisation** ✅ Done.
- [x] **C7 — Docker container hardening (static)** ✅ Done (15
      tests). Dynamic portion deferred (needs Docker daemon).
- [x] **C8 — Multi-account drill** ✅ Done (21 tests).
- [x] **C1 — Coverage cleanup** ✅ Done across 4 strikes:
      validators (1st), universes + jobs + cac40 fix (2nd),
      persistence repos batch 1 (3rd), persistence repos batch 2
      (4th). 10 financial-logic files lifted to ≥ 96–100%;
      125 new tests.
- [x] **C7 dynamic — Container runtime smoke + CVE scan**
      ✅ Done (smoke); CVE scan opt-in (gated on scanner availability).
      Runtime smoke at `tests/webapp/test_container_runtime_smoke.py`
      — 10 tests boot the image under the full Phase-8 C7 flag set
      (`--read-only` + tmpfs + `--cap-drop ALL` +
      `--security-opt no-new-privileges:true` + `--memory 1g` +
      `--pids-limit 256` + `--init` + `--stop-signal SIGTERM`) and
      assert: container boots cleanly + `/health` returns 200 with
      `{"status":"ok",...}`; runtime uid is 10001 (non-root); root
      fs write attempt fails with EROFS; tmpfs `/tmp` is writable;
      `HostConfig.SecurityOpt` carries `no-new-privileges:true`;
      `HostConfig.CapDrop` carries `ALL`; `X-Request-ID`
      round-trips through the live CorrelationMiddleware
      (Phase-8 C2); container emits JSON-line structured logs
      with `category` + `corr_id` keys; only port 8000/tcp is
      EXPOSEd; SIGTERM grace exits the container in < 30 s. CVE
      scan at `tests/webapp/test_container_cve_scan.py` — single
      test driving whichever scanner is available (`trivy` /
      `grype` / `docker scout`) against the built image; asserts
      no fixable CRITICAL/HIGH CVEs with an empty allow-list.
      Both files gated behind `@pytest.mark.docker`; the CVE
      scan additionally `@pytest.mark.cve_scan` so CI opts in
      explicitly. Default `pytest` skips both. New `cve_scan`
      marker registered in `pyproject.toml`. Full suite: 2 684 →
      2 694 (10 new); 1 skipped (CVE scan, no scanner on dev box).
- [x] **C4 — Operator-token rotation + lifecycle (CR-024)** ✅ DONE 2026-05-26 @ `<this commit>`. Full lifecycle cascade landed in two commits: design (SRS / SDS / SDD / TP all stamped 2026-05-26) + implementation. SRS adds REQ_F_TOK_001..005 + REQ_NF_TOK_001 to §3.18 as a new "Operator-token lifecycle" sub-section. SDD adds REQ_SDD_TOK_001..005 to §13.17. Test Plan adds TC_TOK_001..010 + TC_OPS_001 to §3.15c. Implementation: (1) `trading_system/persistence/migrations/0007_token_revocations.sql` adds the `operator_token_revocations` table keyed on `(account_id, jti)`; (2) `trading_system/persistence/repositories/token_revocations.py` ships `OperatorTokenRevocationRepository` + `TokenRevocation` dataclass — write-once-append + idempotent re-revoke + `is_revoked` lookup + `list_all` deterministic iteration; (3) `trading_system/accounts/token_verifier.py` rewritten to support four-segment tokens `<iso>:<aid>:<jti>:<sig>` (legacy three-segment continues to verify, grandfathered — disambiguated by jti's 32-char hex shape vs signature's 64-char), `previous_secret` slot + `rotate_secret(new)` atomic flip, `revocation_lookup` field (duck-typed Protocol accepting either `bool` or `Result[bool, str]` returns), `seconds_until_expiry(token) -> Option[int]` read-only accessor that does NOT emit a SECURITY log, `_audit(...)` helper emitting structured-log entries under the new `LogCategory.SECURITY` value carrying `event`/`account_id`/`outcome`/`jti`/`token_hash` (sha256, never the raw token); (4) `trading_system/observability/logger.py` adds `"security"` to the `LogCategory` Literal; (5) `trading_system/cli.py` adds `trading-bot issue-token --account-id <id> [--ttl <s>] [--secret-env <name>]` subcommand — env-var-only secret discipline (no `--secret <hex>` argv flag); (6) `trading_system/webapp/auth_deps.py::verify_any_valid_claim` fixed to call the shared `_parse_token` helper (the legacy `rsplit(':', 2)` was mis-parsing four-segment tokens; the fix preserves browser-VIEW endpoint auth for both formats); (7) `Documentations/Operations.md` §6 fully rewritten — new CR-024 token format + `trading-bot issue-token` CLI + in-process `rotate_secret` rolling rotation (no restart needed) + new §6.4 "Token revocation" workflow + §6.5 "Token loss". 42 new tests across `tests/accounts/test_token_verifier_cr024.py` (27 tests covering format + back-compat + revocation precedence + multi-secret + seconds_until_expiry + structured audit + replay determinism + household-claim round-trip), `tests/persistence/test_token_revocations_repository.py` (10 tests — migration schema audit + round-trip + idempotent re-revoke + cross-account isolation + cross-restart durability + sorted-list-all + scoped-list + empty-jti rejection), `tests/test_cli.py::test_issue_token_*` (5 tests — happy path + missing env var exit-1 + no `--secret` argv flag introspection + custom env var + non-positive TTL rejection). All 11 new REQs (REQ_F_TOK_001..005, REQ_NF_TOK_001, REQ_SDD_TOK_001..005) at TEST. Full suite 2 694 → 2 724.
- [~] **C3 / C9..C14** — Phase-8 Part C hardening; partial
      progress in this session:
      - **C3** Pydantic v2 schemas for ALL typed-loader YAMLs
        ✅ DONE 2026-06-01 @ `<this commit>` (four-slice
        delivery; 11 of 11 typed YAMLs covered). New module
        `trading_system/config/pydantic_schemas.py` ships
        models for: `notifications.yaml` (channels + retry +
        approval + Slack / Email sub-configs, with
        cross-field invariant for email-without-settings),
        `risk.yaml`, `kill_switch.yaml`,
        `mc_drawdown_floor.yaml`, `system.yaml`,
        `turbos.yaml`, `webui.yaml`, `logging.yaml`,
        `phases.yaml` (the largest: 5-bound monotonicity +
        per-phase allocation_targets sum-to-one across the
        closed AllocationBucket enum + risk-band invariant +
        nullable portfolio_vol_cap), `quant.yaml` (validator
        bounds_table with per-row {lo, hi} cross-field
        invariant + overfitting ratio_max / ic_floor ranges),
        and `accounts.yaml` (multi-account list with
        duplicate-id rejection + closed tax_model set).
        `RICH_SCHEMAS` table drives the validator.
        `validate_with_pydantic_schemas(config_dir)` collects
        every field-level violation in one pass (the
        existing dataclass loaders stop at the first miss).
        `trading-bot validate-config --rich-errors` flag opts
        in to the tree-shaped output. 66 schema tests + 2 CLI
        tests; runtime path stays on the existing dataclass
        loaders for back-compat. Documented pattern: extra
        fields rejected via `extra='forbid'` (catches typos
        the existing loaders silently ignored); cross-field
        invariants via an optional `cross_field_errors()`
        method on the top-level model or per-field validators
        reading `info.data`; Decimal coercion via
        `mode='before'` validators so YAML int/float/string
        literals all parse to canonical Decimals.
      - **C9** ks-incident postmortem CLI ✅ DONE 2026-06-01 @
        `<this commit>`. New `trading-bot ks-incident
        --since <iso> [--until <iso>] [--account-id <id>]
        [--db <path>] [--table]` CLI subcommand. New
        `KillSwitchSnapshotRepository.list_in_window(*, since,
        until)` returns ``Result[tuple[AuditSnapshot, ...], str]``
        in `captured_at ASC` order (timeline shape). Bounds
        closed; ``None`` on either side means open. CLI emits
        canonical-JSON timeline by default (sorted keys; ready
        for `jq` / Grafana / Splunk ingestion) or a
        human-readable table via `--table`. Account-scoped via
        the repo's `account_id` slot. 13 new tests (6 repo + 7
        CLI; the CLI tests cover happy JSON output / table mode
        / invalid --since / --until cutoff / empty window /
        missing db / etc.).
      - **C10** list-backtests search DSL ✅ DONE 2026-06-01
        @ `<this commit>`. New `trading-bot list-backtests
        [--account-id <id>] [--strategy <id>] [--since <iso>]
        [--metric "<expr>"...] [--db <path>] [--json]` CLI
        subcommand. New `BacktestArchiveRow` dataclass +
        `BacktestResultRepository.list_archived(*, account_id,
        strategy_id=None, since=None)` method. Operator-facing
        metric DSL: `name<op>value` with closed vocabulary
        `{final_equity, max_drawdown, realized_after_tax,
        trades_count, knockouts}` + ops `{>, >=, <, <=, ==}`.
        Multiple `--metric` flags AND. Output: table by
        default, machine-readable JSON via `--json`.
        Categorised Errs on bad expressions
        (`cli:metric:no_op|unknown_name|bad_value|empty_expression`).
        14 new tests (6 repo + 8 CLI) cover happy paths +
        filter AND-ing + invalid expressions + account
        isolation + since cutoff + archived_at DESC ordering.
      - **C12** /metrics Prometheus endpoint ✅ DONE 2026-06-01
        @ `<this commit>`. New `trading_system/webapp/metrics.py`
        module: 5 metric series (paper-tick / broker-submit /
        persistence-write Histograms + trades-emitted /
        ks-transitions Counters) registered as module-level
        singletons against the default registry. Engine-facing
        helpers (`time_paper_tick` / `time_broker_submit` /
        `time_persistence_write` context managers +
        `record_trade` / `record_ks_transition` counter
        bumps) so engine modules never import prometheus_client
        directly. `GET /metrics` returns the standard Prometheus
        exposition format (`text/plain; version=0.0.4`); no
        auth (the convention is for operators to expose this
        endpoint on an internal-only network or behind a
        reverse proxy). Paper-trading runtime's `tick_once`
        wraps `_apply_bar` in `time_paper_tick` — additional
        instrumentation sites land as a follow-up slice.
        prometheus_client is a SOFT dependency: when not
        installed the module degrades to no-op stubs + the
        endpoint returns 503. New `pip install -e .[metrics]`
        extra; CI install step extended to include it. 10
        new tests at `tests/webapp/test_metrics.py` cover
        endpoint Content-Type / metric series rendering /
        each `time_*` context manager / each `record_*`
        counter / dep-absent 503 path. OpenAPI snapshot
        regenerated to include the new route.
      - **C13** /reports/compare view route ✅ DONE 2026-06-01
        @ `<this commit>`. New `GET /reports/compare?a=<job_a>
        &b=<job_b>` route in `webapp/routers/views/reports.py`
        + `templates/reports_compare.html` template. Loads
        `summary.json` from each bundle to render a KPI
        comparison table (final equity / max_drawdown /
        realized_after_tax / dividends_after_tax / trades_count
        / knockouts); side-by-side iframes of each bundle's
        `equity-curve.html`. Path-traversal-safe via the
        existing `_report_dir()` helper. Route declared BEFORE
        the `/{job_id}` catch-all so FastAPI's first-match
        ordering picks it up. 7 new tests at
        `tests/webapp/test_reports_view.py` (9 → 16) covering
        auth gate / missing query params / 404-on-missing-
        bundle / path-traversal rejection / happy-path KPI
        rendering / graceful summary-missing fallback. OpenAPI
        snapshot regenerated.
      - **C11 / C14** — shipped earlier (yfinance BarSource
        + paper-session metadata).

**Session result (2026-05-26):** 2 519 → 2 724 tests (+205;
includes the +30 from CR-021 / CR-022 cascade work + the +30 from
CR-024 cascade earlier in the same session). 5 CRs filed (CR-021 /
CR-022 / CR-024 Accepted with full cascade; CR-023 Proposed).
21 commits pushed across wiki + main repo. Operations.md to v1.0
+ §6 rewritten for CR-024. Persistence-layer
coverage rises ~83% → ~98% on the 5 in-scope repos.

---

## Roadmap to full webapp version

The operator-grade webapp (CR-019 step 1) is feature-complete for
paper trading + backtest workflow + report archive + strategy
registry + recovery wizard + multi-account switcher +
accessibility audits. **Phase 8 closed the hardening gate.**
Reaching the **full webapp version** (operator can run live
trading + every feature has a clean operator workflow) requires
the open work below.

Tracked separately from the lifecycle phases because each slice
has its own CR cascade or operator-driven decision (broker
selection, secret-store choice, etc.). Items are ordered by
expected effort + dependency.

### 1. Live-trading mode (CR-019 step 2) — broker-agnostic surface ✅ landed

- [x] **CR-019 step 2 SRS + SDS + SDD + TP — broker-agnostic** ✅ DONE
      2026-05-26. Full cascade landed adding 17 new REQs
      (REQ_F_LIV_001..008, REQ_NF_LIV_001, REQ_SDD_LIV_001..007,
      REQ_SDS_WEB2_005). The concrete adapter is its own
      separate cascade per REQ_F_BRK_003.
- [x] **Live-runtime composition layer** at
      `trading_system/webapp/runtimes/live_trading.py` ✅ DONE
      2026-05-26. Ships `LiveTradingSession` + `LiveTradingRuntime`
      + `LiveRuntimeRegistry`; same external Protocol surface as
      `PaperTradingRuntime` (`tick_once` / `stop` / `is_alive` +
      a new `submit_order` for the audited submit path). No
      concrete-broker imports; broker semantics live entirely
      behind the `BrokerAdapter` Protocol (REQ_SDD_LIV_002).
      Per-account KS gate consulted before every submit
      (REQ_F_LIV_006).
- [x] **Live-order persistence** ✅ DONE 2026-05-26. New migration
      `0008_live_orders.sql` + `LiveOrderRepository` with
      `record_submit_intent` / `record_submitted` /
      `record_rejected` / `list_pending` / `get`. Distinct
      transactions for pre-submit + post-submit so the write
      lock is not held across the broker call (REQ_SDD_LIV_006).
      20 new tests at
      `tests/persistence/test_live_orders_repository.py`.
- [x] **Operator pre-flight CLI** ✅ DONE 2026-05-26.
      `trading-bot live-preflight --config-dir <path> [--out
      <path>]` runs the six documented gates in order
      (broker_selector → broker_authenticate → operator_token →
      kill_switch → persistence_integrity → market_data), writes
      a canonical-JSON artefact to `var/live-preflight.json`,
      short-circuits on first failure (subsequent gates land as
      `"skipped"`). 9 preflight tests + 2 CLI smoke tests.
- [ ] **Concrete `<Broker>BrokerAdapter` implementation** — gated
      on the operator's broker selection (XTB / Saxo / IBKR / ...).
      The lifecycle ships `LocalBrokerAdapter` as the conformance
      baseline; every live adapter MUST pass the existing
      `tests/integration/test_broker_conformance_drill.py` suite
      before reaching production.
- [x] **FastAPI live-mode routes** ✅ DONE 2026-05-30. Four routes
      under `/api/accounts/{account_id}/`: `live-mode/enable`,
      `live-mode/disable`, `emergency-stop`, `broker-reconnect`.
      All four per-account-token gated (REQ_F_LIV_008); household
      claim REJECTED on all four with categorised
      `live:household_claim_rejected` Err. Every authorised
      action emits a `LogCategory.SECURITY` audit entry with
      `event` + `account_id` + `outcome` + `token_hash` — raw
      token never in the payload (REQ_NF_TOK_001). Domain
      semantics delegate to three small Protocol slots on
      `WebappState` (`live_mode_controller` /
      `emergency_stop_controller` / `broker_reconnect_controller`)
      so the routes test without the full live runtime. 31 new
      tests at `tests/webapp/test_live_mode_routes.py` covering:
      household claim rejected on all 4 routes (parameterised);
      mismatched-account token rejected; matching token accepted;
      enable/disable/emergency-stop/broker-reconnect happy paths
      invoke their controllers; categorised HTTP responses on
      controller Err (403 / 409 / 502); SECURITY audit emitted
      on every authorised action with token_hash present + raw
      token absent (parameterised over all four routes);
      controller-missing 500. OpenAPI snapshot regenerated.
- [x] **Dashboard live-mode panel + chip enablement** ✅ DONE
      2026-05-30. `trading_system/webapp/routers/views/dashboard.py`
      gains a `_live_mode_status(request)` helper computing the
      `live` chip enablement state — reads `var/live-preflight.json`
      (or the operator-overridden path on `app.state.
      live_preflight_artefact`) + checks `app.state.broker_selector`
      (set by the boot wiring; the views layer doesn't read
      `trading_system.config.*` directly per the structural
      audit). The template's three-position mode switch flips
      the `live` chip from disabled-with-tooltip to enabled when
      ALL of: artefact present + `outcome="ok"` + `checked_at`
      within 30 s + selector != "local"; otherwise stays
      disabled with the categorised reason in
      `data-live-mode-reason` + a tooltip naming the unmet
      precondition + the recovery hint (`trading-bot
      live-preflight`). The live-trading panel renders when
      `?mode=live` AND the chip is enabled — surface mirrors the
      paper-trading panel (equity / open positions / today's P&L
      / rejection counter / broker connectivity / emergency stop
      / broker reconnect) per REQ_F_LIV_003. 9 new tests at
      `tests/webapp/test_live_mode_dashboard.py` covering the
      four chip-disable states (missing / failed / stale /
      local-broker), the enable path, the panel rendering when
      mode=live + chip enabled, the no-render when mode=paper or
      chip disabled, and the broker_selector + checked_at
      threading. Legacy `tests/webapp/test_mode_switch.py::
      test_live_mode_is_disabled_with_documented_tooltip` updated
      to assert the new tooltip shape (categorised `live:` reason
      + recovery hint + `data-live-mode="disabled"` marker).
      Lifts REQ_F_LIV_003 + REQ_F_LIV_008 + REQ_SDD_LIV_005 from
      TP to TEST. All 17 CR-019 step 2 REQs now at TEST.

### 2. Notification adapters (CR-001 Phase B)

- [x] **`SlackNotificationChannel`** ✅ DONE (already shipped in
      an earlier CR-018 slice). Slack incoming-webhook adapter at
      `trading_system/notifications/channels/slack.py` reading
      from `TRADING_BOT_SLACK_WEBHOOK_URL` env var per
      REQ_NF_NOT_003 (env-var name resolved lazily on every
      delivery so rotated webhooks land without restart).
- [x] **`EmailNotificationChannel`** ✅ DONE (already shipped in
      an earlier slice). SMTP adapter at
      `trading_system/notifications/channels/email.py` reading
      password from `TRADING_BOT_SMTP_PASSWORD` per
      REQ_SDD_NOT_007 / REQ_NF_NOT_003. STARTTLS by default;
      port 465 implicit-TLS path supported via
      `use_starttls: false`.
- [x] **`notifications/loader.py` selector** ✅ DONE 2026-05-31
      @ `<this commit>`. The loader-side gap is closed:
      `_CHANNEL_SELECTORS` extended from `{"local_log"}` to
      `{"local_log", "slack", "email"}`; two new frozen
      sub-configs `SlackChannelConfig` + `EmailChannelConfig`;
      YAML parser handles the `notifications.slack` +
      `notifications.email` sub-sections with categorised
      `config:schema:` Errs on type mismatches +
      `config:invariant:` Errs on missing-field invariants
      (email requires `smtp_host`/`smtp_port`/`user`/`from_addr`/
      `recipients` — no useful defaults). New
      `build_channels(config, *, extra=())` factory function
      turns the loaded `NotificationsConfig` into concrete
      `NotificationChannel` instances; preserves the YAML
      selector order for replay determinism; `extra` lets the
      webapp append its `InboxChannel` without YAML coupling.
      18 new tests at `tests/notifications/test_loader.py`
      cover the new sub-configs, schema/invariant Err paths,
      and the factory (44 loader tests total).
- [x] **`config/notifications.yaml` sample** ✅ DONE 2026-05-31
      @ `<this commit>`. The 9th YAML file already shipped;
      this slice updates the sample to document the new
      `slack` + `email` channel selectors with full sub-section
      examples (commented out by default; operators uncomment
      the channels they want). Env-var-only secret discipline
      preserved in the comments per REQ_NF_NOT_003.
- [x] **Webapp inbox panel wire-up** ✅ DONE 2026-06-01 @
      `<this commit>`. The FastAPI `webapp/inbox` panel now
      receives every payload broadcast through the runtime
      fan-out — dashboard alerts land in both the inbox AND
      the operator's configured external channels (Slack /
      email) simultaneously.
      Implementation:
      - `trading_system/webapp/inbox.py` — `InboxChannel`
        gains a `deliver(payload) -> Result[None, str]`
        method satisfying the CR-001 `NotificationChannel`
        Protocol. A new `_payload_to_inbox_entry(payload)`
        helper adapts every `NotificationPayload` variant
        (KillSwitchEvent / AnomalyAlert / Summary /
        TradeApprovalRequest / ApprovalResponse / Error)
        into an `InboxEntry` with the documented severity +
        category mapping. The helper is duck-typed over the
        payload union so adding a new payload type extends
        this surface in one place.
      - `trading_system/webapp/app.py` — new
        `build_notification_fanout(*, inbox, config_dir=None)`
        helper loads `config/notifications.yaml` via the
        existing `load_notifications_config` + builds channels
        via `build_channels(cfg, extra=(inbox,))` + wraps in
        a `NotificationFanOut` whose retry policy mirrors the
        YAML's `retry:` sub-section. Defensive fallback:
        missing YAML ⇒ `NotificationsConfig()` defaults;
        present-but-broken YAML ⇒ structured-log envelope +
        defaults (webapp keeps booting). `default_app()`
        calls the helper after constructing the inbox + adds
        the fanout to `WebappState` (new `notification_fanout`
        slot) which lifts onto `app.state.notification_fanout`
        for routes / safety-layer to consume.
      - `_default_config_dir()` helper resolves the config
        directory: honours `TRADING_BOT_CONFIG_DIR` env var;
        defaults to the repo-bundled `config/` directory.
      Tests: 5 new at
      `tests/webapp/test_notification_fanout_composition.py`
      cover bundled-default behaviour, slack-opt-in, invalid-
      YAML fallback, retry-policy override, and end-to-end
      dispatch landing in the inbox. The existing
      `tests/webapp/test_inbox.py` 17-test surface stays
      green; full webapp + notifications suite at 715 passing
      (one CVE-scanner test skipped).

### 3. Operator hypothesis surface (CR-002 Phase B / CR-027)

- [x] **CR-027 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30 @
      `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS §3.21
      adds REQ_F_QNT_007..010 in a new "Operator hypothesis-filing
      surface" sub-section; SDS §3.26 amended; SDD §13.20 adds
      REQ_SDD_QNT_009..012; Test Plan adds TC_QNT_OPS_001..006.
- [x] **Implementation slice** ✅ DONE 2026-05-30 @ `<this commit>`.
      Three JSON routes + one view route + HTML template.
      The webapp routes consume the hypothesis layer via Protocol
      slots (`hypothesis_filer`, `hypothesis_lister`,
      `improvement_report_lookup`) so REQ_NF_QNT_001 +
      REQ_SDD_FAS_001 boundaries hold. Concrete adapters
      `StrategyLabHypothesisFiler` + `StrategyLabHypothesisLister`
      live at `trading_system/strategy_lab/quant/webapp_adapter.py`
      and are wired by operator code at boot. Files:
      `trading_system/webapp/routers/api/hypotheses.py` (POST +
      GET + per-strategy lineage); `trading_system/webapp/
      routers/views/hypotheses.py` (HTML view); `trading_system/
      webapp/templates/hypotheses.html` (form + two tables).
      Auth gating mirrors CR-019 step 2 (`HOUSEHOLD_CLAIM`
      rejected; cross-account rejected; missing Authorization
      ⇒ 401). LogCategory.SECURITY audit on every authorised
      `file` action carrying `event` / `account_id` / `outcome`
      / `token_hash` / `hypothesis_id` per CR-024 shape.
      12 new tests at `tests/webapp/test_hypotheses_api.py`
      cover all 6 TC_QNT_OPS_001..006 cases. All 8 CR-027 REQs
      at TEST. Full suite 2 862 → 2 877.

### 4. Stdlib webui Phase B (CR-004 Phase B)

The FastAPI surface (CR-017) covers most operator paths; the
stdlib `webui/` fallback still has placeholders.

- [x] Concrete `routes/summary.py` body. ✅ DONE 2026-05-31 @
      `<this commit>`. REQ_F_WEB_002 (b) financial summary read
      endpoint. Handler factory + `SummaryReader` Protocol; path
      shape `/accounts/<aid>/summary`; household-claim auth gate;
      canonical JSON response via the existing `SummaryResponse`
      schema. 5 tests (happy path / per-account token reject /
      method reject / malformed path / byte-identical replay).
- [x] Concrete `routes/registry_list.py` body. ✅ DONE 2026-05-31
      @ `<this commit>`. REQ_F_WEB_002 (c) strategy-registry read
      endpoint. `RegistryListReader` Protocol + new
      `RegistryListResponse` schema carrying `RegistryEntryLine`
      tuples. Path shape `/accounts/<aid>/registry`. 3 tests.
- [x] Concrete `routes/backtests_archive.py` body. ✅ DONE
      2026-05-31 @ `<this commit>`. REQ_F_WEB_002 (d) backtest-
      archive paginated read endpoint. Path shape
      `/accounts/<aid>/backtests` with `?per_page=<n>&page=<n>`
      query params (default 25/1; per_page capped at 100;
      negative values rejected as `webui:per_page_out_of_bounds:*`
      / `webui:page_out_of_bounds:*`). New `BacktestsArchiveResponse`
      schema. 5 tests (default pagination / explicit pagination /
      per_page bounds reject / page bounds reject / byte-identical
      replay).
- [x] Concrete `routes/improvement_reports_history.py` body. ✅
      DONE 2026-05-31 @ `<this commit>`. REQ_F_WEB_002 (e)
      ImprovementReport history read endpoint. Path shape
      `/accounts/<aid>/improvement-reports`. New
      `ImprovementReportsHistoryResponse` schema. 3 tests.
      All four routes follow the live_state.py reference shape
      (Reader Protocol + handler factory + canonical JSON +
      household-claim auth + REQ_NF_WEB_002 byte-identical
      replay). 16 new tests at `tests/webui/test_phase_b_routes.py`.
      Routes registered in `webui/routes/__init__.py` exports.

### 4b. CR-025 — `PaperBrokerAdapter` (paper trading as a broker adapter)

- [x] **CR-025 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30 @
      `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS §3.33
      adds REQ_F_PAP_011..014; SDS §3.4 documents
      `PaperBrokerAdapter` + selector table extended to
      `{"local", "paper"}`; SDD §13.32 adds REQ_SDD_PAP_001..005;
      Test Plan adds TC_PAP_BRK_001..006.
- [x] **`PaperBrokerAdapter` implementation** ✅ DONE 2026-05-30 @
      `<this commit>`. `trading_system/execution/paper.py` —
      `@dataclass(slots=True)` wrapping `LocalBrokerAdapter`,
      `submit()` calls `market_data.latest(instrument)` and
      synthesises a `Tick` from the bar close ± `spread_bps/2`;
      no credential surface (REQ_F_PAP_011); passes the existing
      broker conformance suite via a stubbed `MarketDataProvider`
      (REQ_F_PAP_012); `config/system.yaml`'s `broker.adapter`
      accepts `"paper"` and the preflight CLI's
      `broker_selector` gate accepts it as well (REQ_F_PAP_013 /
      REQ_F_PAP_014). Factory at
      `trading_system/webapp/runtimes/preflight_broker.py`
      (under the documented `webapp/runtimes/` carve-out so
      REQ_SDS_CLI_001 + REQ_SDD_FAS_001 both hold). 14 new tests
      at `tests/execution/test_paper_broker.py` + 1 new test at
      `tests/test_cli.py::test_live_preflight_paper_selector_accepted_at_first_gate`.
      All 9 new REQs (REQ_F_PAP_011..014, REQ_SDD_PAP_001..005)
      at TEST. Full suite 2 816 → 2 831.

### 4c. CR-026 — Multi-instrument paper-trading runtime + dashboard grid

- [x] **CR-026 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30 @
      `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS §3.33
      adds REQ_F_PAP_015..018; SDS §3.39 amended + new §3.39c
      documents `MultiInstrumentBarSource`; SDD §13.33 adds
      REQ_SDD_PAP_006..010; Test Plan adds TC_PAP_MULTI_001..006.
- [x] **`MultiInstrumentBarSource` + runtime universe field** ✅
      DONE 2026-05-30 @ `<this commit>`.
      `trading_system/webapp/runtimes/multi_instrument_bar_source.py`
      ships `MultiInstrumentBarSource` — `@dataclass(frozen=True,
      slots=True)` wrapping a `MarketDataProvider`; `poll()`
      iterates the lex-sorted universe and returns
      `Ok({InstrumentId: Bar})` on partial fan-out,
      `Err("data:no_bars")` only when EVERY symbol fails
      (REQ_F_PAP_016 / REQ_SDD_PAP_008). `PaperTradingRuntime`
      gains a `universe: tuple[Stock, ...]` field normalised
      lex-sorted-by-symbol in `__post_init__`; the legacy
      single-instrument constructor builds a degenerate
      single-symbol universe so backwards-compat holds
      (REQ_SDD_PAP_006). `_build_screener_ranking` now emits one
      `ScoredStock` per universe member (REQ_F_PAP_015 /
      REQ_SDD_PAP_007). `PaperStateResponse` gains a new
      `per_instrument: tuple[InstrumentRow, ...]` field +
      `pinned_symbol: str` — `InstrumentRow` is a new frozen
      dataclass carrying `(symbol, last_close, day_change_pct,
      has_open_position, sparkline)` per universe stock
      (REQ_F_PAP_017 / REQ_SDD_PAP_009). 10 new tests at
      `tests/webapp/test_multi_instrument_paper_runtime.py`. All 9
      new REQs (REQ_F_PAP_015..018 + REQ_SDD_PAP_006..010) at TEST.
      Full suite 2 831 → 2 841.
- [x] **Multi-instrument tick fan-out + dashboard grid template**
      ✅ DONE 2026-05-31 @ `<this commit>`. REQ_F_PAP_018 /
      REQ_SDD_PAP_010 — `_apply_bar` now fans
      `portfolio.mark(...)` out across every universe member
      (not just the primary instrument), so open positions in
      any universe symbol get repriced per tick. The refactor:
      extracted `_poll_universe_bars()` as the shared poll
      helper; `_apply_bar` consumes its output for both
      (a) `portfolio.mark` with `{instrument_id: close}` across
      every successful poll, and (b) the existing CR-029
      `instrument_bar_repo.append_bars(...)` persistence
      fan-out — the universe-wide poll runs at most once per
      tick. Legacy single-instrument sessions (no
      `market_data_provider` OR degenerate universe) fall
      through to the primary-instrument-only mark for
      back-compat. The dashboard's per-instrument grid +
      pin-to-detail-chart JS already shipped with the CR-026
      schema slice; this fan-out closes the runtime gap so the
      universe rows show fresh prices on every poll. 2 new
      tests at `tests/webapp/test_multi_instrument_paper_runtime.py`
      (`test_runtime_marks_portfolio_at_universe_wide_prices_per_tick`
      + `test_legacy_single_instrument_runtime_marks_only_primary`)
      assert the mark fan-out + the legacy single-instrument
      back-compat. Full multi-instrument runtime suite 18 → 20
      tests; broader webapp + conformance suite stays at 581
      passing.

### 4d. CR-028 — Technical-indicator library for the quant layer

- [x] **CR-028 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30 @
      `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS §3.34
      adds REQ_F_IND_001..006 + REQ_NF_IND_001; SDS §3.43;
      SDD §13.34 adds REQ_SDD_IND_001..005; Test Plan
      TC_IND_001..010.
- [x] **`trading_system/quant/indicators/`** ✅ DONE 2026-05-30 @
      `<this commit>`. New `quant/` top-level package with the
      `indicators/` sub-package shipping five pure-function
      helpers — `sma(closes, n)`, `rsi(closes, n=14)`,
      `atr(bars, n=14)`, `obv(bars)`, `adx(bars, n=14)`. Each
      returns a parallel `tuple[Decimal | None, ...]` (or
      `tuple[Decimal, ...]` for OBV) the same length as the
      input; warm-up positions hold `None`. Wilder smoothing for
      RSI/ATR/ADX (canonical TA literature). Decimal-only — float
      operands surface as `TypeError` at the boundary.
- [x] **`trading_system/data/volatility_index.py`** ✅ DONE
      2026-05-30 @ `<this commit>`.
      `VolatilityIndexProvider` runtime-checkable Protocol +
      `YFinanceVolatilityIndexProvider` concrete; closed symbol
      registry (`^VIX` USD, `^VSTOXX` EUR); unknown symbols Err
      with `volatility_index:unknown_symbol:<symbol>` per
      REQ_SDD_IND_004. 21 new tests at
      `tests/quant/test_indicators.py` covering golden values,
      Wilder smoothing, Decimal boundary, determinism (within
      process + across subprocesses), Protocol conformance, and
      runtime-safe import. All 11 new REQs at TEST.
      Full suite 2 841 → 2 862.
- [x] **`StrategyMetrics` extension** ✅ DONE 2026-05-31 @
      `<this commit>`. The `*_signal` Decimal-or-None fields
      already shipped with the CR-028 cascade; this slice adds
      the canonical helpers strategies call to turn the
      readings into a `TradeRationale.signal_reason` string:
      `StrategyMetrics.to_signal_reason() -> str` (instance
      method) + `format_signal_reason(*, sma_200, rsi, atr,
      obv, adx, vix)` (standalone helper at
      `trading_system/strategy_lab/metrics.py`). Both produce
      the same canonical output: `"name=value;name=value;..."`
      sorted alphabetically by indicator name; None values
      omitted; Decimal values render via `str(...)` for
      canonical-decimal stability (REQ_NF_REP_001 family);
      empty string when every signal is None (back-compat).
      8 new tests at `tests/strategy_lab/test_metrics.py` —
      empty / sorted-order / None-skip / Decimal-canonical /
      determinism / delegation / default-empty / full-set.
      Exported from `trading_system.strategy_lab` as
      `format_signal_reason`. Strategies that consume the
      CR-028 indicator library at decision time now have a
      single helper to call when building TradeRationales;
      the audit trail bytes stay deterministic across replays.

### 4e. CR-029 — Multi-instrument bar persistence

- [x] **CR-029 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30 @
      `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS §3.17
      adds REQ_F_PER_011..014; SDS §3.22 + §3.22b amendments; SDD
      §13.35 adds REQ_SDD_PER_010..014; Test Plan TC_PER_BAR_001..007.
- [x] **Implementation slice** ✅ DONE 2026-05-30 @ `<this commit>`.
      `persistence/migrations/0009_instrument_bars.sql` schema +
      cross-symbol index; `persistence/repositories/instrument_bars.py`
      `InstrumentBarRepository` with `append_bar` / `append_bars`
      (single COMMIT for 40-symbol fan-out, idempotent via
      `INSERT OR IGNORE`) / `bars_for` (per-symbol range query
      ordered by `bar_at ASC`) / `bars_at` (cross-symbol slice
      "what was the universe doing at time T"); paper-trading
      runtime gains `instrument_bar_repo` slot + the `_apply_bar`
      fan-out persists every universe symbol's polled bar BEFORE
      the strategy step; webapp gains `GET /api/accounts/{aid}/bars`
      route at `webapp/routers/api/bars.py` (operator-token-gated,
      household-claim REJECTED, canonical-JSON byte-determinism,
      categorised `webapp:missing_query_param:*` /
      `webapp:bad_iso_datetime:*` /
      `webapp:instrument_bar_repository_missing` Errs). 8
      repository tests + 9 route tests + 2 runtime fan-out tests.
      All 9 CR-029 REQs (REQ_F_PER_011..014 + REQ_SDD_PER_010..014)
      at TEST. Full suite 2 884 → 2 903 (+19 new).

### 5. CR-023 — Overlap-tolerant cache fallback

- [x] **CR-023 SRS / SDS / SDD / TP cascade** ✅ DONE 2026-05-30
      @ `<this commit>`. Wiki cascade stamped 2026-05-30 — SRS
      REQ_F_PAP_002 amended (cached-only includes the prefix
      case); SDS §3.3 amendment; SDD adds REQ_SDD_DAT_016; Test
      Plan §3.15.3 adds TC_DAT_C3_001..003.
- [x] **`YFinanceCache.get_bars_overlap` helper** ✅ DONE
      2026-05-30 @ `<this commit>`. Third-pass overlap-tolerant
      scan in `trading_system/data/yfinance/cache.py`. Returns
      bars sliced to `max(file_start, key.start) <= bar.at <=
      min(file_end, key.end)` from the widest-intersection file
      (lex order breaks ties); Nothing() only when zero files
      intersect. Used by the fallback-only path so backtest
      replays keep REQ_NF_DAT_001 byte-equality via the strict
      envelope.
- [x] **`fetch_live_bars` fallback wired through the overlap
      path** ✅ DONE 2026-05-30 @ `<this commit>`.
      `trading_system/data/yfinance/provider.py` swaps the
      network-failure fallback from `cache.get_bars(key)` to
      `cache.get_bars_overlap(key)`. The cached prefix now
      surfaces when `file_end < key.end` instead of returning
      the network Err.
- [x] **TC_DAT_C3_001..003 tests land** ✅ DONE 2026-05-30 @
      `<this commit>`. 4 new tests at
      `tests/data/yfinance/test_cache.py` +
      `tests/data/yfinance/test_provider.py`: intersection
      slice, non-intersecting Nothing(), widest-intersection
      wins, fetch_live_bars overlap fallback returns the
      cached prefix. Full suite 2 905 → 2 908.

### 6. Paper-trading session metadata persistence (CR-019 follow-up)

- [x] **`paper_sessions` persistence table** ✅ DONE 2026-05-31 @
      `<this commit>`. Migration `0010_paper_sessions.sql`
      (numbering corrected — 0008 and 0009 were already used by
      `live_orders` and `instrument_bars`) adds the table with
      `(account_id PK, universe, strategy_id, instrument_symbol,
      starting_capital TEXT, currency, bar_source, started_at,
      mode_tag)` + `idx_paper_sessions_started_at` index.
- [x] **`PaperSessionRepository`** ✅ DONE 2026-05-31 @
      `<this commit>`. `append_session(row)` (renamed from
      `write_session` to honour REQ_SDD_NAM_004), `get(account_id)`,
      `list_all()` ordered by `started_at DESC`. Duplicate
      account_id surfaces as
      `persistence:integrity:paper_sessions:duplicate:<id>` so
      the runtime can offer "stop existing first" rather than
      silently shadowing the prior session.
- [x] **Wizard write** ✅ DONE 2026-05-31 @ `<this commit>`. The
      onboarding finish handler builds the row via the new
      `webapp/runtimes/paper_session_writer.py` adapter (keeps
      the view layer clean per REQ_SDD_FAS_001) + calls
      `paper_session_repo.append_session(row)`. Write failure is
      non-fatal — the session keeps ticking without persisted
      metadata.
- [x] **Boot-resume enrichment** ✅ DONE 2026-05-31 @ `<this commit>`.
      `default_app()` opens the repo via the shared
      `_persistence_connection()` helper. The boot-resume inbox
      entries now carry the session's universe + strategy +
      instrument so the operator sees what was running before the
      restart instead of an opaque account_id.
- [x] **Recovery-wizard one-click rehydration** ✅ DONE 2026-05-31
      @ `<this commit>`. New POST
      `/paper-sessions/{account_id}/rehydrate` route at
      `webapp/routers/views/paper_session.py` calls the new
      `webapp/runtimes/runtime_rehydrator.py::rehydrate_paper_session`
      helper which reads the persisted `PaperSessionRow` +
      rebuilds the runtime via the same `build_runtime` + bar
      source + strategy factory the wizard's finish handler uses
      + calls `registry.start(runtime)`. Idempotent on
      already-running sessions; surfaces categorised
      `paper:rehydrate:{already_running, session_not_found,
      bad_strategy, runtime_failed, register_failed,
      not_configured}` flash cookies for the dashboard banner.
      5 new tests at `tests/webapp/test_paper_session_rehydrate.py`
      cover auth, happy path, idempotency, missing-metadata
      fallback, unwired-persistence fallback. Full suite
      2 973 → 2 977 (+5 — one test gets bundled into the auth
      coverage).

### 7. CR-024 follow-ups

- [x] **Webapp `POST /api/operator/rotate-secret` endpoint** ✅
      DONE 2026-05-31 @ `<this commit>`. Household-token-gated;
      server generates a fresh 64-byte random secret + atomically
      rotates the verifier (`previous_secret` slot keeps existing
      tokens verifying through the grace window) + returns the
      new secret in the canonical-JSON body ONCE for the operator
      to capture. Emits LogCategory.SECURITY audit per
      REQ_NF_TOK_001. Per-account tokens REJECTED with
      `registry:household_required`.
      Lives at
      `trading_system/webapp/routers/api/operator_tokens.py`;
      sibling endpoints:
      - `POST /api/operator/accounts/{aid}/tokens/{jti}/revoke`
        — per-account-token-gated, household REJECTED, idempotent
        on duplicate.
      - `GET /api/operator/accounts/{aid}/tokens/revoked` —
        per-account list of revocation rows.
- [x] **Operator UI for revocation** ✅ DONE 2026-05-31 @
      `<this commit>`. New `GET /operator/tokens` view +
      `operator_tokens.html` template ships three sections:
      "Rotate household secret" form (HTMX-bound to the JSON
      endpoint, confirm-dialog guards against accidental rotation),
      "Revoke an operator token" form (jti pattern-validated to
      32-char hex), and the per-account "Revoked tokens" table
      sourced from the repository. 12 tests at
      `tests/webapp/test_operator_tokens_api.py` cover rotation
      happy path / household-required / per-account rejection,
      revocation happy path / idempotency / household-claim
      REJECTED / cross-account REJECTED / missing-repo, list
      per-account scoping, view rendering / login redirect /
      seeded-revocation rendering.
- [x] **Multi-process revocation propagation** ✅ DONE
      2026-05-31 @ `<this commit>`. Re-scoped after
      investigation. The v1 design described in the original
      bullet (process-local in-memory cache + re-loaded on
      every persistence write) was aspirational — the actual
      shipped code in `OperatorTokenRevocationRepository` does
      a SELECT on every `is_revoked()` call, with no cache
      layer. This is correct under SQLite WAL semantics:
      committed revocations from any process are immediately
      visible to readers in other connections (same host,
      shared SQLite file). Single-host multi-process
      deployments (the v1 target) get cross-process
      revocation propagation for free.
      Two real gaps surfaced + closed in this commit:
      1. **Verifier wire-up gap.** `default_app()` was
         constructing the `AccountScopedTokenVerifier` WITHOUT
         passing `revocation_lookup` — revoked tokens still
         passed auth checks. Wired the repo into the verifier
         so the auth path consults `is_revoked()` BEFORE the
         TTL check (REQ_F_TOK_002 / REQ_SDD_TOK_002).
      2. **Multi-process test gap.** Added two new tests at
         `tests/persistence/test_token_revocations_repository.py`:
         `test_multi_process_revocation_visible_via_shared_sqlite`
         (two repos on shared SQLite file see each other's
         revocations immediately via WAL) +
         `test_multi_process_revoke_idempotent_across_connections`
         (concurrent duplicate revocations land exactly once).
      Repository docstring updated to remove the aspirational
      "in-memory set warmed at startup + re-loaded after every
      write" wording and document the actual SELECT-on-every-
      call + SQLite-WAL approach. Multi-HOST propagation
      (SSE / database-NOTIFY) remains a future-CR scope —
      SQLite is single-host by design, so the trading-bot's v1
      deployment target doesn't need it.

### 8. Known-limitation drills (Validation.md §5)

- [x] **Phase-5+ multi-year mock-data drill** ✅ DONE 2026-05-31
      @ `<this commit>`. New
      `tests/integration/test_multi_year_regime_crossing.py`
      ships three tests: (1) per-segment classification across
      a 7-year synthetic series (BULL → BEAR → BULL) — runs in
      default CI; (2) per-tick traversal asserting ≥ 2
      TransitionEvents with BULL→BEAR + BEAR→BULL pairs
      captured; (3) paired-replay determinism (REQ_NF_DET_001 /
      REQ_NF_REP_001) — two independent walks of the same
      7-year fixture produce tuple-equal TransitionEvent
      sequences. Tests 2-3 carry `@pytest.mark.wallclock` so
      they're excluded from the default CI matrix (they sample
      the detector every 7 bars across 1 800 bars; the per-tick
      growing-window re-eval is O(n²) and runs ~25 s each).
      Operators run them on-demand via
      `pytest -m wallclock tests/integration/test_multi_year_regime_crossing.py`.
      CI workflow updated to exclude the wallclock marker.
- [x] **Multi-account live-runtime drill** ✅ DONE 2026-05-31 @
      `<this commit>`. `tests/integration/test_multi_account_live_runtime.py`
      drives three `PaperTradingRuntime` instances
      (`paper-alpha-2026` / `paper-beta-2026` / `paper-gamma-2026`
      at 1 k / 5 k / 20 k EUR) through a shared `RuntimeRegistry`
      across 10 ticks with distinct stub bar trajectories. Four
      tests cover: (1) registry partitioning + duplicate-id
      rejection (REQ_F_PAP_005), (2) independent equity curves
      per account (no cross-account bleed), (3) per-account stop
      semantics (stopping `beta` leaves `alpha` + `gamma`
      alive + ticking), (4) paired-replay determinism — building
      + ticking the 3-account household twice produces
      byte-identical equity-point sequences per account
      (REQ_NF_DET_001 / REQ_NF_REP_001). Runs in ~1 s, included
      in the default CI matrix. Builds on Phase-8 C8's gate
      semantics (`tests/integration/test_multi_account_drill.py`).
- [x] **Hard-floor MC gate per phase / regime** ✅ DONE 2026-05-31 @
      `<this commit>`. CR-031 cascade landed (SRS §3.27 REQ_F_MCS_005
      amendment + REQ_F_MCS_007 + REQ_NF_MCS_002; SDS §3.32
      amendment with `MCDrawdownFloor` value-object surface +
      `LoopController` context binding; SDD §13.37
      REQ_SDD_MCS_007..009; Test Plan §3.15l.1 TC_MCS_011..014).
      Implementation:
      `trading_system/strategy_lab/mc_drawdown_floor.py` ships
      `MCDrawdownFloor` (`@dataclass(frozen=True, slots=True)`
      with `matrix: frozenset[tuple[Phase, MarketRegime, Decimal]]`
      + `default: Decimal`; constructors `.fixed(value)` /
      `.from_matrix(matrix, *, default)` / `.from_yaml(path)`;
      `floor_for(phase, regime)` lookup; `__post_init__`
      rejects negative defaults + negative matrix entries).
      `LoopController` gains `phase: Phase | None` + `regime:
      MarketRegime | None` optional fields; `mc_drawdown_floor`
      widened to `Decimal | MCDrawdownFloor | None`; isinstance
      dispatch routes matrix vs legacy path; matrix-path
      rejection emits a `LogCategory.IMPROVEMENT_REPORT`
      structured-log envelope with `{strategy_id, phase, regime,
      applied_floor, p5_drawdown, category}` payload (all
      Decimals via `str(...)` for canonical-decimal stability).
      Rejection category string preserved
      (`mc:p5_drawdown_exceeds_phase_floor`) so downstream
      consumers stay schema-stable. New
      `config/mc_drawdown_floor.yaml` (11th typed-config YAML)
      ships the initial grid pinned to CLAUDE.md's phase
      scaling table — Phase 1..3 carry wider floors in BEAR /
      HIGH_VOL, Phase 5+ ratchet tighter. 25 new tests across
      `tests/strategy_lab/test_mc_drawdown_floor.py` (17 tests
      — matrix lookup, determinism, fallback, fixed
      constructor, invariants, YAML loader happy + 7 Err
      paths, bundled-grid smoke) +
      `tests/strategy_lab/test_loop_controller_mc.py`
      (4 new — matrix reject, matrix pass-through,
      legacy-decimal back-compat, structured-log envelope
      shape). All 5 new REQs at TEST (642 → 647 REQs).
      Validation.md §5 third known-limitation closed.

### 9. CI / infrastructure

- [x] **GitHub Actions workflow** ✅ DONE 2026-05-31 @
      `<this commit>`. `.github/workflows/ci.yaml` chains
      structural audits → `traceability-report.py --check` →
      OpenAPI snapshot guard → full pytest (excluding docker /
      cve_scan markers) on every push + PR against `main`.
      Concurrency group on the branch cancels stale runs.
      Python 3.13 + dependencies installed via
      `pip install --require-hashes -r requirements.lock` so
      runs are byte-deterministic across actors.
      `.github/pull_request_template.md` surfaces the
      CLAUDE.md hard-rule-8 documentation-update checklist.
- [x] **CVE scanner provisioning** ✅ DONE 2026-05-31 @
      `<this commit>`. `.github/workflows/docker.yaml` ships a
      separate workflow gated on `workflow_dispatch` + a weekly
      Monday 06:00 UTC cron (upstream CVE database refresh).
      Three jobs: `container-runtime` (Dockerfile + runtime smoke
      + read-only-fs invariants), `container-reproducibility`
      bundled into runtime, `cve-scan` (installs Trivy + runs
      `tests/webapp/test_container_cve_scan.py`). The CVE
      allow-list lives at `Documentations/CVE-Allowlist.md`
      (operator-maintained; every entry SHALL carry a
      justification + re-review date).

### 9c. CR-032 — Operator settings UI

- [x] **CR-032 cascade Phase 1..4 (design)** ✅ DONE
      2026-06-01 @ `<prior commit>`. Wiki cascade stamped
      2026-06-01 — SRS §3.20 adds REQ_F_SET_001..005 +
      REQ_NF_SET_001; SDS §3.46 documents the settings view
      module + atomic YAML writer; SDD §13.38 adds
      REQ_SDD_SET_001..004; Test Plan §3.15q adds
      TC_SET_001..006.
- [x] **CR-032 cascade Phase 5 (implementation)** ✅ DONE
      2026-06-01 @ `<this commit>`.
      `trading_system/webapp/settings_state.py` ships the
      `ReloadPending` frozen dataclass (in-memory slot on
      `app.state.reload_pending`; not persisted across
      restarts per CR-032 question 4).
      `trading_system/webapp/settings_writer.py` ships
      `write_notifications_yaml(config_dir, cfg) ->
      Result[None, str]` using `ruamel.yaml.YAML(typ='rt')`
      for comment-preserving round-trip (CR-032 question 2)
      + write-tempfile-fsync-rename atomicity + categorised
      `webapp:settings:{io,invariant}:<details>` Err set +
      `env_vars_referenced(cfg)` helper.
      `trading_system/webapp/routers/views/settings.py` ships
      three endpoints: `GET /operator/settings` (redirects to
      notifications sub-page), `GET /operator/settings/
      notifications` (HTMX form pre-filled from the on-disk
      YAML), `POST /operator/settings/notifications`
      (validates → writes → updates `app.state.reload_pending`
      → redirects back with error_field/error_message query
      params on failure). Household-claim accepted per
      REQ_F_SET_001; per-account tokens also accepted on read.
      `trading_system/webapp/templates/settings_notifications.html`
      ships the form template with channels / retry / approval
      / paths / Slack / Email sub-sections + env-var status
      indicators (set/unset only; REQ_NF_SET_001 secret
      discipline — never leaks the resolved value).
      `trading_system/webapp/templates/base.html` chrome gains
      the user-menu dropdown (Settings / Log out / About per
      CR-032 question 3 — Logout is no-op + tooltip) +
      reload-pending banner rendered when
      `app.state.reload_pending` is non-None (lists every
      env-var the saved config depends on so the operator
      verifies the secrets pre-restart, per CR-032 question 1).
      `trading_system/webapp/fragments.py::fragment_context`
      extended to surface `reload_pending` to every chrome
      render. New `[settings]` install extra in pyproject.toml
      for the `ruamel.yaml` soft dependency.
      Tests: 11 new at `tests/webapp/test_settings_view.py`
      cover TC_SET_001..006: landing redirect / form pre-fill /
      household + per-account token acceptance / save round-
      trip / validation failure preserves YAML / reload_pending
      populated + not surviving fresh `create_app()` /
      user-menu chrome HTML structure / aria-label / env-var
      NAMES only in the YAML + HTML. OpenAPI snapshot
      regenerated to include the new routes. Conformance
      audit (REQ_SDD_NAM_004) allow-list extended to permit
      `webapp/settings_writer.py:write_notifications_yaml`.
      Full webapp + conformance suite stays green at 617
      passing.

### 10. Deferred (re-triage after live trading lands)

- [ ] **CR-003 — News-feed secondary signal** (🔴 Deferred
      2026-05-16).

---

## Standing Constraints (apply to every phase)

- Optimize **net after-tax** return — never gross.
- Backtests **must** include 30% CTO tax and injection timeline.
- A trade is valid only if `expected_net_profit > 5 × total_fees` after tax.
- Kill switch is non-bypassable; no module may execute trades while it is tripped.
- Claude's role is bounded: generate strategy candidates, refactor, propose filters,
  explain failures. Claude **must not** simulate results, bypass risk constraints, or
  override the backtest engine.
- No phase skipping. No implementation before its gate is approved.
- **Starting capital and broker are not hardcoded.** Starting capital comes from
  config; the broker is selected via the `BrokerAdapter` interface. The system must
  run end-to-end against the mock adapter without any concrete broker configured.
- **Option / Result, not exceptions.** Module-boundary fallible operations return
  `Result[T, E]`; possibly-absent values return `Option[T]`. `try` / `except` is
  forbidden for control flow; `raise` is reserved for panics on programmer-error
  invariants. Third-party exceptions are wrapped at the adapter and converted to
  `Result`. See CLAUDE.md → "Coding conventions" for the full discipline.
- **Every task ends with a documentation update.** Before checking a task complete:
  update `TASKS.md` (`[x]`, date, commit SHA — **and design-only cascades land
  here too as `[ ]` Phase-6 rows with the cascade SHAs so the engineering plan
  reflects every accepted CR even before code lands**), re-run
  `python3 tools/traceability-report.py` and commit the regenerated CSV with
  the code change (the regenerated `Documentations/Traceability.md` goes in
  the matching wiki commit), amend any affected wiki
  document with a re-approval row (per `REQ_NF_LIF_002`) and bump the
  `Documentations/` submodule pointer, and update `CLAUDE.md` / `README.md` when
  rules, conventions, or user-facing status change. The traceability tool's
  `--check` mode is the CI gate for the matrix; the wider rule covers every
  artifact in the repo. See CLAUDE.md hard rule #8 for the full procedure.
