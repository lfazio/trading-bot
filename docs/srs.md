# Software Requirements Specification (SRS)

**Project:** trading-bot
**Lifecycle phase:** Phase 1 — SRS
**Status:** Draft (awaiting review/approval before SDS)
**References:** [`trading-bot.md`](../trading-bot.md) (original spec), [`CLAUDE.md`](../CLAUDE.md), [`tasks.md`](../tasks.md)

> **Lifecycle rule.** No design or code is permitted at this stage. Each requirement
> below carries an ID. Once approved, the IDs are immutable; new requirements are
> appended, never renumbered. Any change after approval restarts the lifecycle from
> the affected phase.

---

## 1. Introduction

### 1.1 Purpose
Define WHAT the system must do — the complete set of functional, non-functional,
safety, and constraint requirements for a Python trading system that optimizes
**after-tax** returns under France CTO taxation and scales capital through gated
phases.

### 1.2 Scope
The system covers: configurable starting capital → phase-driven scaling; broker
abstraction (XTB as reference adapter); EU dividend/swing stock investing;
tactical trading; phase-gated turbo (CFD) usage; structured-products overlay
(≤ 10%); deterministic backtesting; bounded meta-optimization; and a global
kill-switch safety layer.

The system is **not** an autonomous trader. Claude assists in strategy generation
and refactoring; Python deterministically simulates and decides.

### 1.3 Verification methods
Each requirement is verified by one or more of:
- **T** — Test (unit / integration / property)
- **A** — Analysis (proof, formal, calculation, review of derivation)
- **I** — Inspection (code or document review)
- **D** — Demonstration (end-to-end run, drill, observable behavior)

### 1.4 Glossary
- **Phase** — Capital tier (1–6) determining position/trade limits, allocation, drawdown caps.
- **CTO** — *Compte-Titres Ordinaire*; French taxable brokerage account; 30% flat tax (PFU).
- **Turbo** — Knockout-leveraged certificate; risk = invested capital only.
- **Knockout distance** — Distance from current price to barrier, as % of underlying.
- **Walk-forward** — Train / validation / out-of-sample evaluation method.
- **BrokerAdapter** — Interface abstracting any broker (XTB reference impl).
- **Injection** — External capital deposit; excluded from performance metrics.
- **Milestone** — Configured equity threshold that may unlock controlled scaling.

---

## 2. Stakeholders & Roles

| Role | Responsibility |
|---|---|
| Operator | Sole human user; approves phase gates, kill-switch recoveries, broker selection. |
| System (Python) | Deterministic decisioning, simulation, execution, safety enforcement. |
| Claude (LLM) | Generate strategy candidates, propose filters, refactor logic, explain failures. **Forbidden** from simulating results, bypassing risk, or overriding the backtest engine. |

---

## 3. Functional Requirements

### 3.1 Capital & Phase Engine — `REQ_F_CAP`

- **REQ_F_CAP_001** — Starting capital SHALL be read from configuration; no hardcoded value. *V: I, T*
- **REQ_F_CAP_002** — The phase engine SHALL select the active phase from `equity + injected_capital`. *V: T*
- **REQ_F_CAP_003** — The system SHALL define six phases: Capital Builder, Stability, Systematic, Capital Acceleration, Wealth Preservation, Scale/Institutional. *V: I*
- **REQ_F_CAP_004** — Phase boundary thresholds SHALL be configurable in `config/phases.yaml`. *V: I, T*
- **REQ_F_CAP_005** — Phase transitions SHALL apply hysteresis on downgrades to prevent flapping near a boundary. *V: T*
- **REQ_F_CAP_006** — Phase 1 (Capital Builder) SHALL enforce: ≤ 3 positions, ≤ 4 trades/month, allocation 90% dividend / 10% tactical, turbos disabled, max drawdown 15%. *V: T*
- **REQ_F_CAP_007** — Phase 2 (Stability) SHALL enforce: ≤ 6 positions, ≤ 8 trades/month, allocation 70% stocks / 30% tactical, ≤ 1 turbo position with ≤ 5% exposure, max drawdown 15%. *V: T*
- **REQ_F_CAP_008** — Phase 3 (Systematic) SHALL enforce: ≤ 12 positions, ≤ 20 trades/month, allocation 60% core / 40% tactical, turbos enabled with 10–15% exposure cap, max drawdown 20%. *V: T*
- **REQ_F_CAP_009** — Phase 4 (Capital Acceleration) SHALL enforce: ≥ 20 positions, ≥ 40 trades/month, allocation 50% core / 30% tactical / 20% structured (turbos), turbo exposure ≤ 20%, hedging permitted, max drawdown 20%. *V: T*
- **REQ_F_CAP_010** — Phase 5 (Wealth Preservation) SHALL enforce: ≥ 30 positions, ≥ 60 trades/month, allocation tilted to lower-vol core (≈55%) with 15% tactical / 15% structured / 10% turbos / 5% cash, turbo exposure ≤ 15%, hedging required, max drawdown 15%, risk/trade 0.5–1%. *V: T*
- **REQ_F_CAP_011** — Phase 6 (Scale/Institutional) SHALL enforce: ≥ 50 positions, ≥ 100 trades/month, allocation ≈60% diversified core / 15% tactical / 10% structured / 10% turbos / 5% alternatives, turbo exposure ≤ 10%, hedge overlay mandatory, max drawdown 12%, risk/trade 0.25–0.75%. *V: T*
- **REQ_F_CAP_012** — Phases 5–6 SHALL enforce a portfolio-level volatility cap in addition to per-trade limits. *V: T*
- **REQ_F_CAP_013** — Risk per trade limits SHALL be: phases 1–3 = 1–2%, phase 4 = 1–1.5%, phase 5 = 0.5–1%, phase 6 = 0.25–0.75%. *V: T*
- **REQ_F_CAP_014** — Stop-loss SHALL be mandatory in every phase. *V: T*

### 3.2 Capital Flow — `REQ_F_CFL`

- **REQ_F_CFL_001** — The system SHALL track initial capital, every external injection (amount + timestamp), total deployed capital, and equity. *V: T*
- **REQ_F_CFL_002** — Performance metrics SHALL exclude external injections (no inflated returns from deposits). *V: T, A*
- **REQ_F_CFL_003** — Risk sizing SHALL scale with total available capital (initial + injections + retained equity). *V: T*
- **REQ_F_CFL_004** — Backtests SHALL accept and simulate an injection timeline explicitly. *V: T*

### 3.3 Tax Engine — `REQ_F_TAX`

- **REQ_F_TAX_001** — Realized capital gains SHALL be taxed at 30% flat (France CTO / PFU): `net_profit = gross_profit × 0.70`. *V: T*
- **REQ_F_TAX_002** — Dividends SHALL be taxed at 30% flat: `net_dividend = dividend × 0.70`. *V: T*
- **REQ_F_TAX_003** — A trade SHALL be valid only if `expected_net_profit > 5 × total_fees` AFTER tax. *V: T*
- **REQ_F_TAX_004** — All optimizations SHALL target net-after-tax return; gross-return optimization is forbidden. *V: I, A*
- **REQ_F_TAX_005** — Backtests SHALL apply taxes; no exception. *V: T*
- **REQ_F_TAX_006** — Phase 5+ SHALL support tax-loss harvesting (offsetting realized gains with realized losses within the fiscal year). *V: T*

### 3.4 Broker Adapter & Execution — `REQ_F_BRK`

- **REQ_F_BRK_001** — The execution layer SHALL define a `BrokerAdapter` interface covering: order submission, order cancellation, position retrieval, account state, leveraged-instrument support, market-data subscription. *V: I, T*
- **REQ_F_BRK_002** — The system SHALL ship a mock `BrokerAdapter` that supports full end-to-end runs without any live broker. *V: D, T*
- **REQ_F_BRK_003** — The system SHALL ship an XTB (XAPI) reference adapter as the first concrete implementation. *V: T*
- **REQ_F_BRK_004** — The active adapter SHALL be selected by configuration (`broker.adapter`). *V: T*
- **REQ_F_BRK_005** — Non-execution modules SHALL NOT depend on any concrete broker; only on `BrokerAdapter`. *V: I*

### 3.5 Stock Screener — `REQ_F_SCR`

- **REQ_F_SCR_001** — The screener SHALL filter EU equities by: dividend yield 3–7%, payout ratio < 70%, free cash flow > 0, debt/equity < 1.5, ≥ 5 years dividend history. *V: T*
- **REQ_F_SCR_002** — The screener SHALL output a scored ranking with components: stability, yield quality, valuation. *V: T*

### 3.6 Strategy Engine — `REQ_F_STR`

- **REQ_F_STR_001** — The system SHALL implement a *core* strategy: long-term holding, dividend compounding, low turnover. *V: T*
- **REQ_F_STR_002** — The system SHALL implement a *tactical* strategy: trend following, breakout confirmation, pullback entries. *V: T*
- **REQ_F_STR_003** — Every shipped strategy SHALL pass walk-forward validation (train / validation / out-of-sample). *V: T*
- **REQ_F_STR_004** — Phase 6 SHALL operate strategies as a multi-strategy ensemble with vol-targeting and risk-parity allocation. *V: T*

### 3.7 Turbo Selection — `REQ_F_TRB`

- **REQ_F_TRB_001** — The turbo selector SHALL implement a 3-step pipeline: filter → score → select. *V: T*
- **REQ_F_TRB_002** — The filter SHALL reject candidates with knockout distance < 5%, spread > 1.5%, leverage above the phase cap, low liquidity, or extreme volatility. *V: T*
- **REQ_F_TRB_003** — The score SHALL be `0.35·knockout_distance_score + 0.25·leverage_efficiency + 0.20·cost_score + 0.20·expected_move_capture`. *V: T*
- **REQ_F_TRB_004** — If the best score is below threshold, the selector SHALL emit *no trade*. *V: T*
- **REQ_F_TRB_005** — Risk on a turbo position SHALL equal invested capital only; margin assumptions are forbidden. *V: I, T*
- **REQ_F_TRB_006** — Each turbo candidate SHALL declare: underlying, direction, leverage, knockout, spread. *V: I, T*

### 3.8 Structured Products — `REQ_F_STP`

- **REQ_F_STP_001** — Total structured-product allocation SHALL be ≤ 10% of portfolio. *V: T*
- **REQ_F_STP_002** — Every product SHALL be decomposed into: equity-equivalent exposure, hidden leverage estimate, worst-case loss, break-even probability. Non-decomposable products SHALL be rejected. *V: T*
- **REQ_F_STP_003** — Structured products SHALL be deployed only in low-vol / sideways / stable-macro regimes. *V: T*
- **REQ_F_STP_004** — Structured products SHALL be blocked in high-vol / crisis / liquidity-stress regimes. *V: T*
- **REQ_F_STP_005** — Each candidate SHALL pass stress tests (crash, vol expansion, correlation spike) and a liquidity check (exit constraints, early-redemption risk). *V: T*
- **REQ_F_STP_006** — The system SHALL enforce issuer diversification across structured products. *V: T*
- **REQ_F_STP_007** — Stacking a structured product and a turbo on the same underlying SHALL be forbidden. *V: T*

### 3.9 Risk Engine — `REQ_F_RSK`

- **REQ_F_RSK_001** — The risk engine SHALL enforce per-phase max drawdown (15/15/20/20/15/12%). *V: T*
- **REQ_F_RSK_002** — Single-asset exposure SHALL be capped at 25–35% (phase-dependent). *V: T*
- **REQ_F_RSK_003** — Portfolio correlation SHALL be monitored; correlation stacking SHALL trigger rebalancing or rejection. *V: T*
- **REQ_F_RSK_004** — Phase 5+ SHALL enforce a portfolio-level volatility cap. *V: T*
- **REQ_F_RSK_005** — Risk-engine failure or inconsistent output SHALL trip the kill switch. *V: T, D*

### 3.10 Backtesting Engine — `REQ_F_BCT`

- **REQ_F_BCT_001** — The backtest engine SHALL be deterministic given a seed and inputs. *V: T*
- **REQ_F_BCT_002** — Backtests SHALL simulate broker fees parameterized by adapter (spreads + commissions). *V: T*
- **REQ_F_BCT_003** — Backtests SHALL simulate slippage. *V: T*
- **REQ_F_BCT_004** — Backtests SHALL simulate turbo knockouts. *V: T*
- **REQ_F_BCT_005** — Backtests SHALL simulate dividends. *V: T*
- **REQ_F_BCT_006** — Backtests SHALL apply 30% CTO tax. *V: T*
- **REQ_F_BCT_007** — Backtests SHALL simulate the explicit external-capital-injection timeline. *V: T*
- **REQ_F_BCT_008** — Walk-forward (train/validation/OOS) SHALL be required for every strategy candidate; OOS collapse SHALL cause rejection. *V: T*
- **REQ_F_BCT_009** — Phase 5+ SHALL use extended walk-forward windows covering multiple regime crossings. *V: T*

### 3.11 Portfolio System — `REQ_F_PRT`

- **REQ_F_PRT_001** — The portfolio SHALL track: cash, positions, realized gains, dividends, after-tax equity curve. *V: T*
- **REQ_F_PRT_002** — Phase 6 SHALL produce NAV-style reporting and P&L attribution. *V: T*
- **REQ_F_PRT_003** — Diversification SHALL be enforced; single-asset dominance SHALL be rejected. *V: T*

### 3.12 Dashboard — `REQ_F_DSH`

- **REQ_F_DSH_001** — The dashboard SHALL display: current phase, allocation, turbo exposure, after-tax performance, drawdown, trade history. *V: D*

### 3.13 Milestone Controller — `REQ_F_MIL`

- **REQ_F_MIL_001** — The system SHALL define a configurable milestone list (default: 2k, 5k, 10k, 20k, 50k, 100k, 200k, 500k, 1M, 2M, 5M €). *V: I, T*
- **REQ_F_MIL_002** — A milestone SHALL only validate when all conditions hold: stable returns AND low drawdown AND strategy consistency AND no recent kill-switch events. *V: T*
- **REQ_F_MIL_003** — Milestone scaling SHALL be gradual (≤ +10–20% exposure increase); exponential or leverage-explosion scaling SHALL be forbidden. *V: T*
- **REQ_F_MIL_004** — The controller SHALL reject "fake growth" signals (overfitting gains, vol-spike profits, single-trade anomalies). *V: T*

### 3.14 Meta-Optimization Loop — `REQ_F_MTO`

- **REQ_F_MTO_001** — The meta-loop SHALL be a bounded research engine, not autonomous trading. *V: I*
- **REQ_F_MTO_002** — The loop SHALL run an 8-step pipeline: generate → backtest → risk filter → overfitting test → score → select → registry → deployment gate. *V: T*
- **REQ_F_MTO_003** — Final candidate score SHALL be `0.4·net_return_after_tax + 0.3·sharpe + 0.2·stability + 0.1·drawdown_penalty`. *V: T*
- **REQ_F_MTO_004** — A candidate SHALL pass walk-forward validation; OOS performance collapse SHALL cause rejection. *V: T*
- **REQ_F_MTO_005** — The strategy registry SHALL version candidates; validated entries SHALL be immutable. *V: T*
- **REQ_F_MTO_006** — A new strategy SHALL be accepted only if `new_risk ≤ baseline_risk` AND `new_return / new_risk > baseline`. *V: T, A*
- **REQ_F_MTO_007** — Each cycle SHALL emit an `ImprovementReport` with best strategy id, deltas (return/drawdown/sharpe), risk assessment, rejected candidates, rejection reasons. *V: T*
- **REQ_F_MTO_008** — Strategies SHALL be evaluated across regimes (bull / bear / sideways / high-vol); failure in any regime SHALL cause rejection or downgrade. *V: T*

---

## 4. Safety Requirements — `REQ_S_KS` (Kill Switch)

- **REQ_S_KS_001** — The kill switch SHALL define three states: ACTIVE, DEGRADED, KILL. *V: I*
- **REQ_S_KS_002** — Override priority SHALL be `KillSwitch > RiskEngine > StrategyLogic > ExecutionLayer`. No component may override the kill switch. *V: I, A*
- **REQ_S_KS_003** — Financial triggers SHALL include: drawdown > phase limit, single-day loss > threshold, rapid equity decline (X% in Y days). *V: T*
- **REQ_S_KS_004** — Strategy-instability triggers SHALL include: persistent backtest degradation, walk-forward collapse, optimizer reward collapse, unstable regime variance. *V: T*
- **REQ_S_KS_005** — Execution-anomaly triggers SHALL include: repeated broker order rejection, abnormal slippage / spread expansion, missing or corrupted data feeds. *V: T*
- **REQ_S_KS_006** — System-integrity triggers SHALL include: risk-engine failure, validator no-response, registry corruption, anomalous meta-optimizer behavior. *V: T*
- **REQ_S_KS_007** — On KILL, the system SHALL: cancel pending orders, freeze new execution, disable auto-execution, freeze strategy updates, log a full state snapshot, alert the operator. *V: T, D*
- **REQ_S_KS_008** — DEGRADED mode SHALL: reduce position sizes by ≥ 50%, disable turbos, restrict to top-tier confidence trades, increase validation strictness, enforce extra risk buffer. *V: T*
- **REQ_S_KS_009** — Recovery from KILL SHALL require all of: drawdown back below threshold, system integrity restored, backtests stable, **manual operator confirmation**. *V: T, D*
- **REQ_S_KS_010** — Kill-switch conditions SHALL NOT be modifiable at runtime. *V: I, T*
- **REQ_S_KS_011** — No module SHALL execute trades while KILL is active. *V: T, A*
- **REQ_S_KS_012** — The system SHALL prefer stopping incorrectly over trading incorrectly when in doubt. *V: A, I*

---

## 5. Non-Functional Requirements — `REQ_NF`

- **REQ_NF_DET_001** — The backtest engine SHALL be fully deterministic given the same seed and inputs. *V: T*
- **REQ_NF_REP_001** — Strategy versions SHALL be reproducible from the registry (same inputs → same outputs). *V: T*
- **REQ_NF_AUD_001** — A full state snapshot SHALL be persisted on every kill-switch event. *V: T, D*
- **REQ_NF_TRC_001** — Every requirement SHALL be traceable to design (SDS), detailed design (SDD), code, and test. *V: I*
- **REQ_NF_LIF_001** — Development SHALL follow the DO-178C-inspired phase-gated lifecycle (SRS → SDS → SDD → Test Plan → Implementation → Test Execution → Validation). *V: I*
- **REQ_NF_LIF_002** — Any change to an upstream artifact SHALL restart the lifecycle from the affected phase. *V: I*
- **REQ_NF_LOG_001** — All trades, kill-switch events, and meta-loop reports SHALL be logged with timestamps. *V: T*

---

## 6. Constraints — `REQ_C`

### 6.1 Tax model
- **REQ_C_TAX_001** — Tax model SHALL be France CTO (PFU) 30% flat on realized gains and dividends. Other regimes are out of scope. *V: I*

### 6.2 Claude (LLM) role
- **REQ_C_CLA_001** — Claude SHALL be limited to: generating strategy candidates, refactoring logic, proposing filters/regime detection, explaining failures. *V: I*
- **REQ_C_CLA_002** — Claude SHALL NOT: simulate results, bypass risk constraints, override the backtest engine, modify kill-switch conditions, or execute trades. *V: I, A*

### 6.3 Behavioral
- **REQ_C_BHV_001** — The system SHALL prefer stocks over turbos unless a strong, validated edge exists. *V: I*
- **REQ_C_BHV_002** — The system SHALL avoid overtrading, especially in early phases. *V: T*
- **REQ_C_BHV_003** — Marginal trades SHALL be auto-rejected. *V: T*
- **REQ_C_BHV_004** — The system SHALL prioritize survival over return. *V: A, I*
- **REQ_C_BHV_005** — Forbidden behaviors: aggressive leverage scaling after milestone, continuous risk increase, overfitting-driven optimization loops, kill-switch bypass, "all-in" trades. *V: I, T*

---

## 7. Acceptance / Output Requirements — `REQ_O`

- **REQ_O_001** — The repository SHALL deliver a runnable Python project with no pseudo-code and no missing modules. *V: D*
- **REQ_O_002** — `main.py` SHALL demonstrate end-to-end: connect (mock or selected broker adapter) → run screener → generate trades → apply phase logic → simulate portfolio → display after-tax results. *V: D*
- **REQ_O_003** — Starting capital, broker selection, and phase thresholds SHALL be read from configuration. *V: I, T*

---

## 8. Approval

This document is **DRAFT**. The Phase 1 → Phase 2 (SDS) gate is not opened until
the operator reviews and approves this SRS. Approval SHALL be recorded in this
section with a date, reviewer name, and a hash of the approved revision.

| Date | Reviewer | Revision (git SHA) | Outcome |
|---|---|---|---|
| 29/04/2026 | Laurent Fazio | _pending_ | OK |
