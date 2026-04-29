# Trading Bot — Task Breakdown

Source of truth: [`trading-bot.md`](./trading-bot.md). All tasks below trace back to that
spec. The lifecycle is DO-178C-inspired and **gated** — no task in a later phase may start
until the gate of the previous phase is reviewed and approved.

Legend: `[ ]` open · `[~]` in progress · `[x]` done · `(REQ_xxx)` traceability id

---

## Phase 0 — Repository Bootstrap

- [ ] Decide Python version (3.11+) and pin in `pyproject.toml`
- [ ] Set up project skeleton (`pyproject.toml`, `ruff`, `mypy`, `pytest`, `pre-commit`)
- [ ] Add `Makefile` (or `just`) with: `lint`, `typecheck`, `test`, `backtest`, `run`
- [ ] Add `.env.example` for broker credentials (never commit secrets); document one
      block per supported adapter
- [ ] Add `config/` defaults: `starting_capital`, `currency`, `phase_thresholds`,
      `broker.adapter` (selects which `BrokerAdapter` implementation to use)
- [ ] Initialize traceability matrix file (`docs/traceability.csv` — REQ ↔ SDS ↔ SDD ↔ code ↔ test)

**Gate:** repo skeleton reviewed; no business logic yet.

---

## Phase 1 — SRS (Software Requirements Specification) ✅ APPROVED 2026-04-29 @ 7424909

Output: `docs/srs.md`. **No design or code allowed.**

- [ ] Functional requirements (REQ_F_xxx)
  - [ ] Capital lifecycle: configurable starting capital; six-phase scaling
  - [ ] After-tax optimization (France CTO, 30% flat)
  - [ ] Tax-aware trade gate: `expected_net_profit > 5 × total_fees AFTER TAX`
  - [ ] EU dividend + swing stock investing
  - [ ] Tactical trading (weeks–months)
  - [ ] Turbo / CFD trading with strict, phase-capped selection
  - [ ] Generic broker adapter interface; XTB (XAPI) as reference implementation
  - [ ] Capital flow tracking (initial + injections, performance net of inflows)
  - [ ] Structured products overlay (max 10%, regime-gated)
  - [ ] Meta-optimization loop (bounded strategy research, not autonomous trading)
  - [ ] Global kill switch (3 states: ACTIVE / DEGRADED / KILL)
  - [ ] Milestone controller (2k / 5k / 10k / 20k / 50k / 100k / 200k / 500k / 1M / 2M / 5M €; thresholds configurable)
  - [ ] Phase 5 capabilities: tax-loss harvesting, sector rotation, currency hedging
  - [ ] Phase 6 capabilities: vol-targeting, risk parity, multi-strategy ensemble, hedge overlay, NAV/attribution reporting
- [ ] Non-functional requirements (REQ_NF_xxx)
  - [ ] Determinism in backtest engine
  - [ ] Reproducibility of strategy versions
  - [ ] Auditability (full state snapshot on kill switch)
- [ ] Constraints (REQ_C_xxx)
  - [ ] Tax: 30% on realized gains and dividends
  - [ ] Risk per trade: phase 1–3 = 1–2%, phase 4 = 1–1.5%, phase 5 = 0.5–1%, phase 6 = 0.25–0.75%; stop-loss mandatory
  - [ ] Max drawdown: phases 1–2 = 15%, phases 3–4 = 20%, phase 5 = 15%, phase 6 = 12%
  - [ ] Portfolio-level vol cap mandatory in phases 5–6 (in addition to per-trade limits)
  - [ ] Position limits: stocks 25–35%, turbos phase-capped (≤ 5 / 15 / 20 / 15 / 10 % across phases 2–6)
  - [ ] Starting capital and phase thresholds are read from `config/`, never hardcoded
- [ ] Assign REQ id to every requirement; populate traceability matrix col 1

**Gate:** SRS explicitly reviewed and approved before SDS.

---

## Phase 2 — SDS (System Design Specification) ✅ APPROVED 2026-04-29 @ 26ce913

Output: `docs/sds.md`. **No low-level code allowed.**

- [ ] High-level architecture diagram (modules + data flows)
- [ ] Module decomposition (must match spec layout)
  - `config/`, `data/`, `models/`, `screener/`, `strategies/`, `risk/`, `tax/`,
    `backtesting/`, `portfolio/`, `execution/`, `phase_engine/`, `turbo_selector/`,
    `dashboard/`, `safety/`, `strategy_lab/`, `milestone_controller/`, `analytics/`
- [ ] External interfaces
  - [ ] Generic `BrokerAdapter` interface (orders, positions, leveraged instruments, account state)
  - [ ] XTB (XAPI) reference adapter contract — first concrete implementation
  - [ ] Market data source(s); pluggable behind a `MarketDataProvider` interface
- [ ] Phase engine design (auto-detect by `equity + injected_capital`; thresholds from config; six phases)
- [ ] Kill switch override hierarchy: `KillSwitch > RiskEngine > Strategy > Execution`
- [ ] Strategy lab orchestration (generator → backtester → evaluator → risk_guard → optimizer → registry → loop_controller)

**Gate:** every SRS REQ id maps to an SDS component; traceability matrix col 2 filled.

---

## Phase 3 — SDD (Software Design Description)

Output: `docs/sdd.md`. Pseudo-code only; map 1:1 to SDS.

- [ ] Class/data-structure design per module
- [ ] Algorithms
  - [ ] Tax module: `net_profit = gross * 0.70`; `net_dividend = dividend * 0.70`
  - [ ] Tax-aware trade gate function
  - [ ] Turbo selection: filter → score → select (with weights 0.35/0.25/0.20/0.20)
  - [ ] Risk engine constraints (drawdown, position, per-trade)
  - [ ] Phase engine state machine (phases 1–6, configurable thresholds, hysteresis on downgrades)
  - [ ] Phase 5 modules: tax-loss harvester, sector rotator, currency hedger
  - [ ] Phase 6 modules: vol-target sizer, risk-parity allocator, strategy ensemble, hedge-overlay manager, NAV/attribution reporter
  - [ ] Capital flow accounting (excludes injections from performance metrics)
  - [ ] Meta-loop scoring: `0.4*ret + 0.3*sharpe + 0.2*stability + 0.1*dd_penalty`
  - [ ] Walk-forward validator
  - [ ] Kill switch trigger evaluator + recovery checker
  - [ ] Milestone controller (gradual +10–20% exposure unlock)
  - [ ] Structured product decomposer (reject if not decomposable)
  - [ ] Regime detector (bull / bear / sideways / high-vol)

**Gate:** traceability matrix col 3 (SDD) complete; reviewed against SDS.

---

## Phase 4 — Test Plan Design

Output: `docs/test_plan.md`. **No implementation allowed.**

- [ ] Unit test plan per module
- [ ] Integration test plan (screener → strategy → risk → execution)
- [ ] Backtest validation plan (deterministic; tax-inclusive; injection timeline)
- [ ] Risk validation tests (drawdown caps, position caps, turbo cap)
- [ ] Tax correctness tests (gross→net; dividend; trade-gate boundary)
- [ ] Edge cases: market crash, turbo knockout, broker rejection, data outage
- [ ] Walk-forward / out-of-sample tests for every strategy candidate
- [ ] Kill switch tests (all trigger categories + recovery + manual override)
- [ ] Structured product stress tests (crash / vol expansion / correlation spike)

**Gate:** every REQ has ≥ 1 test case; traceability matrix col 4 complete.

---

## Phase 5 — Implementation

Follow the spec's mandatory order. Each module must reference its REQ ids in module
docstring.

1. [ ] `models/` — domain entities (Position, Trade, Order, Instrument, Turbo, ...)
2. [ ] `data/` — market data layer (cache, feeds, validation)
3. [ ] `tax/` — France CTO tax engine + tax-aware trade gate
4. [ ] `execution/` — generic `BrokerAdapter` interface + mock + XTB (XAPI) reference adapter (orders, positions, turbos/CFDs)
5. [ ] `phase_engine/` — phase detection + constraint enforcement
6. [ ] `screener/` — EU dividend/stock screener (yield 3–7%, payout <70%, FCF>0, D/E<1.5, ≥5y history)
7. [ ] `strategies/` — core (long-term/dividend) + tactical (trend, breakout, pullback)
8. [ ] `turbo_selector/` — filter + score + select (phase-gated)
9. [ ] `risk/` — risk engine (drawdown, position, per-trade, stop-loss enforcement)
10. [ ] `backtesting/` — deterministic engine (fees, slippage, knockouts, dividends, **tax**, injections)
11. [ ] `portfolio/` — cash, positions, gains, dividends, **after-tax equity curve**
12. [ ] `dashboard/` — phase, allocation, turbo exposure, after-tax perf, drawdown, history

Cross-cutting (build alongside):

- [ ] `safety/` — `kill_switch.py`, `monitor.py`, `anomaly_detector.py`, `state_manager.py`, `alert_system.py`
- [ ] `strategy_lab/` — `generator.py`, `backtester.py`, `evaluator.py`, `risk_guard.py`, `optimizer.py`, `registry.py`, `loop_controller.py`
- [ ] `milestone_controller/` — milestone gate + gradual exposure unlock + fake-growth detector
- [ ] `structured_products/` — classifier, decomposer, regime filter, allocation cap (0–10%)
- [ ] `capital_flow/` — injection tracking, performance net of inflows
- [ ] `analytics/` — performance + monitoring; phase 6 NAV/attribution reporter
- [ ] `wealth_ops/` — phase-5 features: tax-loss harvester, sector rotator, currency hedger
- [ ] `institutional/` — phase-6 features: vol-target sizer, risk-parity allocator, strategy ensemble, hedge-overlay manager
- [ ] `main.py` — runnable demo: connect (mock or selected broker adapter) → screener → trades → phase logic → portfolio sim → after-tax results; reads starting capital from config

---

## Phase 6 — Test Execution

- [ ] Run unit suite (target ≥ 90% coverage on financial-logic modules)
- [ ] Run integration suite
- [ ] Run backtests on historical data (multiple regimes)
- [ ] Walk-forward validation on every shipped strategy (extended windows for phase 5+)
- [ ] Phase-5 drills: tax-loss harvest correctness, currency-hedge P&L attribution
- [ ] Phase-6 drills: vol-target tracking error, risk-parity weights stability, ensemble decorrelation
- [ ] Edge-case tests (crash, knockout, broker rejection, feed corruption)
- [ ] Broker-adapter conformance tests run against mock adapter and every concrete adapter (XTB first)
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
