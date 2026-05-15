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

- [x] `safety/` — concrete kill-switch implementation ✅ DONE 2026-05-05 (StateManager single writer per REQ_SDS_MOD_010; AuditSnapshot + MemorySnapshotSink + FileSnapshotSink for REQ_NF_AUD_001; AlertChannel Protocol with MemoryAlertChannel + deliver_with_retry exponential backoff per REQ_SDD_ERR_005; pure anomaly detectors single_day_loss_breach + rapid_decline_breach per REQ_SDD_ALG_006/007; HmacOperatorTokenVerifier + RecoveryConditions for REQ_S_KS_009; YAML loader for config/kill_switch.yaml. Standalone `monitor.py` deferred — risk engine + state manager already cover the financial trigger path; strategy / execution / integrity monitors will land alongside backtesting and execution.)
- [x] `strategy_lab/` — `generator.py`, `backtester.py`, `evaluator.py`, `risk_guard.py`, `optimizer.py`, `registry.py`, `loop_controller.py` ✅ DONE 2026-05-08 (2 commits: f13a6b5 metrics + scoring + evaluator + risk_guard + optimizer + registry; this commit generator + backtester wrapper + LoopController + integration test. Pipeline (REQ_F_MTO_002): generate → backtest → evaluate → risk_guard → walk-forward (optional) → score (REQ_F_MTO_003 weights pinned 0.4/0.3/0.2/0.1) → optimizer accept (REQ_F_MTO_006 strict-improvement comparator) → registry.store (validated=False; operator promotes via mark_validated) → ImprovementReport (REQ_F_MTO_007). Runtime imports only `strategy_lab.registry` per REQ_SDS_MOD_014; everything else is operator-driven.)
- [x] `milestone_controller/` — milestone gate + gradual exposure unlock + fake-growth detector ✅ DONE 2026-05-08 (DEFAULT_MILESTONES = 2k/5k/10k/20k/50k/100k/200k/500k/1M/2M/5M EUR per REQ_F_MIL_001; configurable via constructor. evaluate() requires every gate (stable + low_dd + consistent + no recent KS + not fake-growth) per REQ_F_MIL_002. Exposure unlock pct fixed in [0.10, 0.20] band per REQ_F_MIL_003 — exponential / leverage-explosion scaling unrepresentable. Fake-growth detector trips on any of: 30d gain > 30%, single-trade share > 50%, realized vol > 2x rolling per REQ_SDD_ALG_015. Single-shot semantics: register_crossed advances the ladder; milestones below capital_flow.initial auto-skipped.)
- [x] `structured_products/` — classifier, decomposer, regime filter, allocation cap (0–10%) ✅ DONE 2026-05-08 (admit() runs 6 gates in order: regime (BULL/SIDEWAYS only per REQ_F_STP_003/004), decomposability (REQ_F_STP_002 / REQ_SDS_MOD_008 — non-decomposable rejected before allocation math), turbo-stack ban (REQ_F_STP_007), 10% allocation cap (REQ_F_STP_001), 25% issuer concentration (REQ_F_STP_006 / REQ_SDD_ALG_014 — note: shadowed by 10% SP cap unless operator raises it for SP-heavy mandates), stress (REQ_F_STP_005 / REQ_SDD_ALG_013 — crash -20% × (1+leverage), vol -30%, corr -15%). Per-payoff decomposers for AUTOCALL / BARRIER / CAPITAL_PROT / LEV_CERT (REQ_SDD_ALG_012). Portfolio gained has_turbo_on() + issuer_concentration() helpers.)
- [x] `capital_flow/` — injection tracking, performance net of inflows ✅ DONE 2026-05-08 @ 82ab5ce (CapitalFlow ledger: total_capital, cumulative_injected_at, equity_excl_injections; observe re-sorts per REQ_SDD_ALG_017; consumed by the backtest engine's InjectionScheduler.)
- [x] `data/yfinance/` — Yahoo Finance backtest historical-data adapter (CR-009) ✅ DONE 2026-05-08 (4 commits: 90db016 cache + mappers + symbols + tests; 1f4f4d1 provider + retry + live-mode panic + tests; db7612d engine integration + JSON Lines fixtures; this commit recorder + pyproject extra + closeout. 19 new REQs reach TEST: REQ_F_DAT_001..010, REQ_NF_DAT_001, REQ_SDS_DAT_001..004, REQ_SDD_DAT_010..013. Adapter is backtest-only — constructor panics on run_mode=="live"; cache is system of record for replay determinism; yfinance + pandas behind an optional `[yfinance]` extra and lazy-imported only by the recorder script and the provider's network branch.)
- [x] `persistence/` — SQLite + thin-mapper durable state layer (CR-008) ✅ DONE 2026-05-14 (2 commits: a401caa foundation — Connection (PRAGMA-pinned WAL), MigrationRunner (SHA-locked, idempotent), 0001_init.sql (7 tables, every row carries `account_id` so CR-006 multi-account fits without schema break), Decimal/datetime mappers, PortfolioRepository; this commit RegistryRepository (HMAC-gated promotion — raw token never persisted, only SHA-256 hash + audit row), BacktestResultRepository (archive → lookup round-trips bit-identically on the replay tuple), KillSwitchSnapshotRepository (drop-in SnapshotSink Protocol replacement for FileSnapshotSink — `safety.snapshot_backend: filesystem | persistence` toggle keeps the legacy JSON-lines path available). 23 PER REQs at TEST: REQ_F_PER_001..010, REQ_NF_PER_001, REQ_SDS_PER_001..004, REQ_SDD_PER_001..008. 36 persistence tests; full suite 1001 passed.
- [ ] `accounts/` — multi-account aggregate + AccountRegistry + PortfolioGroup + cross-account risk gate (CR-006, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ fc4df1c, SDS @ 65b87b0, SDD @ ccbe24d, TP @ 708ac25). 23 REQs at TP: REQ_F_ACC_001..010, REQ_NF_ACC_001, REQ_SDS_ACC_001..004, REQ_SDD_ACC_001..008. Phase-6 implementation is a code-only change to repository call sites — CR-008's `account_id` columns + DEFAULT_ACCOUNT_ID sentinel already provide the surface. No Phase-5 code change needed.
- [ ] `notifications/` — NotificationChannel Protocol + ApprovalGate + SummaryPublisher + multi-channel fan-out (CR-001, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ a243820, SDS @ 8738c04, SDD @ ba3db40, TP @ c803b82). 23 REQs at TP: REQ_F_NOT_001..008, REQ_NF_NOT_001..003, REQ_SDS_NOT_001..004, REQ_SDD_NOT_001..008. Inherits CR-006's account-aware token claim + CR-008's SHA-256-token-hash audit pattern. Wires `safety/alert_system.py` consumption through `NotificationFanOut` so the existing KS-only operator config keeps working.
- [ ] `webui/` — stdlib-HTTP API + small SPA for monitoring / summary / registry-promotion / async backtests (CR-004, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ dd82381, SDS @ d944e73, SDD @ c669f55, TP @ 691c322). 24 REQs at TP: REQ_F_WEB_001..010, REQ_NF_WEB_001..002, REQ_SDS_WEB_001..004, REQ_SDD_WEB_001..008. HTTP server runs in a child process so a crash never propagates to trading. Mutation surface is exactly one endpoint (registry promotion); import-graph audit forbids reaching `execution/`.
- [ ] `strategy_lab/quant/` — Hypothesis layer with five-gate validator + overfitting-aware StrategyMetrics + ImprovementReport.hypothesis_ids traceability (CR-002, Phase 6) — design cascade complete 2026-05-15 (4 wiki commits: SRS @ 88f9569, SDS @ 222c83c, SDD @ 8b78c4a, TP @ de0470d). 20 REQs at TP: REQ_F_QNT_001..006, REQ_NF_QNT_001..002, REQ_SDS_QNT_001..004, REQ_SDD_QNT_001..008. Inherits CR-008's HypothesisRepository slot + CR-001's AnomalyAlert fan-out (rejected-hypothesis notification). Closes the design-only batch.
- [ ] `wealth_ops/fx_hedger/` — Phase-5 currency hedger (CR-011) — Accepted (full Phase-5 cascade) 2026-05-15; SRS / SDS / SDD / Test Plan + code pending. Sequenced after CR-013 / CR-014 / CR-015 in the Phase-5 implementation queue per the acceptance log.
- [x] `models/rationale.py` + `analytics/rationale.py` — Trade rationale audit trail (CR-015) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 0575943, SDS @ 2772a3b, SDD @ 73aa62f, TP @ 92873b4, closeout). `TradeRationale` frozen dataclass with REQ_F_RAT_002 shape (trade_id / strategy_id / strategy_version / signal_reason / risk_approval / tax_gate_decision / improvement_report_id / decided_at); non-empty trade_id+strategy_id invariants; hashable + structurally equal across Mapping types; `GATE_VOCABULARY` constant + `validate_gate_vocabulary` audit helper for the closed gate-name set `{tax_gate, kill_switch, risk_per_trade, stop_loss, class_cap, correlation, regime, cross_account_concentration}`. `BacktestResult.rationales: tuple[TradeRationale, ...] = ()` defaulting to empty for backwards compat (length-aligned with trades when non-empty). `analytics.rationale_for(result, trade_id) -> Option[TradeRationale]` is the public read surface. CR-008's persistence mapper (`backtest_result_to_json` / `_from_json`) extended to round-trip rationales bit-identically; older archives without the field round-trip as empty (TC_RAT_010 verifies). 27 tests; 9 RAT REQs at TEST: REQ_F_RAT_001..005, REQ_SDS_RAT_001, REQ_SDD_RAT_001..003.
- [x] `data/fundamentals/` — CSV-seeded fundamentals provider (CR-014) ✅ DONE 2026-05-15 (5 wiki commits + 1 main-repo commit: SRS @ 6cd233b, SDS @ c13bfab, SDD @ f87821f, TP @ 7e19ab6, closeout). `CSVFundamentalsProvider` loads `data/seed_fundamentals.csv` at construction (14 EU dividend stocks), validates schema + numeric + as_of_date + duplicate-id, freezes the snapshot, supports `fundamentals()` only — every other MarketDataProvider method returns `data:not_supported:csv_only` so a mis-wired caller fails fast. `CompositeFundamentalsProvider` chains 2+ providers, first-Ok wins, last-Err loses; empty composite surfaces `data:not_supported:composite_empty`. `tools/fundamentals_csv_template.py` emits a header-only stub for new universes. Refresh hook for operator tooling. 26 tests; 10 FND REQs at TEST: REQ_F_FND_001..005, REQ_NF_FND_001, REQ_SDS_FND_001, REQ_SDD_FND_001..003.
- [x] `regime/` — Market-regime detector + transition tracker (CR-013) ✅ DONE 2026-05-15 (2 commits: bb25b71 foundation — RegimeDetector with public RULE_ORDER + MA-crossover + vol-band rule, TransitionTracker with single mutable cursor + confirmation window + flip-back reset + `from_seed` restart hook, RegimeConfig with invariants, BarSource Protocol; this commit closeout — RegimeOrchestrator wires detector → tracker → SafetyLayer.raise_trigger + TransitionRepository persistence + ast audit forbidding KillSwitch.set_state, CR-008 follow-up TransitionRepository + 0002_regime.sql migration + transition_event_to_row / row_to_transition_event mappers, restart rehydration via repo.latest() + TransitionTracker.from_seed). 14 RGM REQs at TEST: REQ_F_RGM_001..006, REQ_NF_RGM_001, REQ_SDS_RGM_001..002, REQ_SDD_RGM_001..005. 45 regime + transition-repo tests; full suite 1046 passed. The detector is now the runtime's sole MarketRegime source — consumers (sector_rotator, structured_products, risk-engine regime gate) receive the regime as input through the orchestrator's tick boundary instead of computing or hand-setting their own.
- [ ] `analytics/` — performance + monitoring; phase 6 NAV/attribution reporter
- [~] `wealth_ops/` — phase-5 features: tax-loss harvester, sector rotator, currency hedger — **tax-loss harvester** shipped 2026-05-08 under `tax/harvest.py` (REQ_F_TAX_006 at TEST). **Sector rotation** shipped 2026-05-14 via CR-010 (Done) under `wealth_ops/sector_rotator/` — taxonomy + regime_sector_bias + RotationPolicy + HoldingState + SectorRotator; 18 REQs at TEST (REQ_F_SCT_001..007, REQ_NF_SCT_001, REQ_SDS_SCT_001..003, REQ_SDD_SCT_001..007). **Currency hedging** still pending under **CR-011** (Proposed); implementation begins on lifecycle cascade after operator acceptance.
- [~] `institutional/` — phase-6 features: vol-target sizer, risk-parity allocator, strategy ensemble, hedge-overlay manager — partial 2026-05-08: **vol-target sizer** + **risk-parity allocator** + **strategy ensemble** all live in `strategies/ensemble.py` (REQ_F_STR_004 / REQ_SDD_ALG_010 at TEST). **NAV / attribution reporting** lives in `portfolio.attribution()` + `analytics.Analytics` (REQ_F_PRT_002 at TEST). **Hedge-overlay manager** has no SRS REQ — filed as **CR-012** (Proposed); implementation begins on cascade after operator acceptance.
- [x] `main.py` — runnable demo: connect (mock or selected broker adapter) → screener → trades → phase logic → portfolio sim → after-tax results; reads starting capital from config ✅ DONE 2026-05-08 (trading_system/main.py: loads config/system.yaml + config/phases.yaml + config/risk.yaml; builds a 3-stock EU dividend universe (ASML/BNP/SAN) with hand-registered fundamentals on the deterministic mock provider; runs screener → CoreStrategy → Backtest → Dashboard; prints after-tax summary. CLI: `python -m trading_system.main --start ... --end ... [--with-slippage]`. Smoke test in tests/test_main.py covers the success path, the err path on missing config dir, and the slippage branch. REQ_O_001..003 reach TEST.)

---

## Phase 6 — Test Execution

- [ ] Run unit suite (target ≥ 90% coverage on financial-logic modules)
- [ ] Run integration suite
- [ ] Run backtests on historical data (multiple regimes)
- [ ] Walk-forward validation on every shipped strategy (extended windows for phase 5+)
- [ ] Phase-5 drills: tax-loss harvest correctness, currency-hedge P&L attribution
- [ ] Phase-6 drills: vol-target tracking error, risk-parity weights stability, ensemble decorrelation
- [ ] Edge-case tests (crash, knockout, broker rejection, feed corruption)
- [ ] Broker-adapter conformance tests run against `LocalBrokerAdapter` (and any future live-broker adapter once added)
- [ ] Kill switch trip/recovery drill
- [ ] Structured product stress + liquidity drill

---

## Phase 7 — Validation & Traceability

- [ ] Produce final requirement traceability matrix (REQ → SDS → SDD → code → test)
- [ ] Coverage report (requirements coverage, code coverage)
- [ ] Document known limitations
- [ ] Write `docs/operations.md` (kill switch operator runbook + manual recovery procedure)

**Lifecycle rule:** any change after Phase 7 restarts the lifecycle from the affected phase.

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
