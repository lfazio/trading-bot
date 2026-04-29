# System Design Specification (SDS)

**Project:** trading-bot
**Lifecycle phase:** Phase 2 — SDS
**Status:** Draft (awaiting review/approval before SDD)
**SRS revision under design:** 7424909 (approved 2026-04-29)
**References:** [`srs.md`](./srs.md), [`CLAUDE.md`](../CLAUDE.md), [`tasks.md`](../tasks.md)

> **Lifecycle rule.** No low-level code is permitted at this stage. Every component
> below references the SRS requirement IDs it satisfies; absence of a reference is
> a defect. The SDS itself is the artifact required by the DO-178C-inspired
> lifecycle (REQ_NF_LIF_001). After approval, any change re-opens the lifecycle
> from this phase (REQ_NF_LIF_002).

---

## 1. Introduction

### 1.1 Purpose
Define the system architecture: the layers, modules, data flows, and external
interfaces that together satisfy the SRS. The SDS does not specify algorithms,
class internals, or APIs at the function level — those are deferred to Phase 3
(SDD).

### 1.2 Design principles
- **Layered, downward dependencies only.** Higher layers depend on lower; lower
  layers never import from higher (REQ_NF_TRC_001).
- **Pure-core, side-effects-at-edges.** Engines (tax, risk, phase, screener,
  turbo, scoring, backtest) are pure functions of inputs. I/O lives in adapters.
  This is what makes determinism (REQ_NF_DET_001) and reproducibility
  (REQ_NF_REP_001) achievable.
- **Broker- and data-source-agnostic core.** Only the execution and data
  adapter modules know about XTB or any other concrete provider
  (REQ_F_BRK_001, REQ_F_BRK_005).
- **Safety as a first-class layer.** The kill switch sits *above* execution and
  cannot be bypassed; no other module may call execution without first checking
  it (REQ_S_KS_002, REQ_S_KS_011).
- **Configuration over code.** Starting capital, phase thresholds, broker
  selection, risk limits, KS triggers — all from `config/`, none hardcoded
  (REQ_F_CAP_001, REQ_F_CAP_004, REQ_F_BRK_004, REQ_O_003).

### 1.3 Notation
- `REQ_xxx_yyy_NNN` — SRS requirement reference; the traceability tool harvests
  these to populate `docs/traceability.csv`.
- `module/` — directory in the source tree.
- `Type` — a domain entity defined in `models/`.
- `~Iface` — a Python `Protocol` / abstract interface.

---

## 2. Architectural Overview

### 2.1 Layer cake (top depends on bottom; never the reverse)

```
                ┌──────────────────────────────────────────────────┐
  L7 Glue       │ main.py · dashboard/ · analytics/                │
                ├──────────────────────────────────────────────────┤
  L6 Research   │ strategy_lab/ (meta-optimization, bounded)       │
                ├──────────────────────────────────────────────────┤
  L5 Sim        │ backtesting/ (deterministic, tax-aware)          │
                ├──────────────────────────────────────────────────┤
  L4 Decision   │ strategies/ · portfolio/ · milestone_controller/ │
                ├──────────────────────────────────────────────────┤
  L3 Engines    │ tax/ · risk/ · phase_engine/ · screener/         │
                │ turbo_selector/ · structured_products/           │
                │ capital_flow/ · safety/ (kill switch)            │
                ├──────────────────────────────────────────────────┤
  L2 Adapters   │ data/ (MarketDataProvider) ·                     │
                │ execution/ (BrokerAdapter)                       │
                ├──────────────────────────────────────────────────┤
  L1 Models     │ models/ (pure data; no I/O)                      │
                ├──────────────────────────────────────────────────┤
  L0 Config     │ config/ (YAML, validated at startup)             │
                └──────────────────────────────────────────────────┘
```

**Override.** `safety/` is logically L3 but enforces a **veto** over L2 and L4:
no execution call proceeds while the kill switch is in `KILL`, and risk-engine
inconsistency raises a kill-switch trigger (REQ_S_KS_002, REQ_F_RSK_005).

### 2.2 Process model
Single-process, event-driven loop. The runtime alternates between:

1. **Tick** — receive market update from `MarketDataProvider`.
2. **Decide** — strategies produce candidate trades (no execution yet).
3. **Filter** — tax gate, risk engine, phase engine, kill switch.
4. **Execute** — surviving trades go to `BrokerAdapter`.
5. **Account** — portfolio updates; capital flow updates; equity curve recorded.
6. **Audit** — log entry; KS monitors evaluate triggers.

Backtests run the same loop with simulated `MarketDataProvider` and `BrokerAdapter`,
which is what guarantees that backtest behavior matches live behavior
(REQ_F_BCT_001, REQ_NF_DET_001).

---

## 3. Module Decomposition

Each module entry lists: **purpose · inputs · outputs · key REQ ids**. Internal
algorithms are deferred to the SDD.

### 3.1 `config/` — L0
- **Purpose:** load, validate, and freeze configuration at startup. Single
  source of all tunables.
- **Files:** `system.yaml`, `phases.yaml`, `risk.yaml`, `tax.yaml`,
  `turbos.yaml`, `structured.yaml`, `kill_switch.yaml`, `meta_loop.yaml`.
- **Inputs:** YAML files; environment variables for secrets (broker credentials).
- **Outputs:** typed `Config` object passed to every module that needs it.
- **Validates:** schema, value ranges (e.g., percentages in `[0, 1]`), cross-field
  invariants (e.g., phase-N upper bound = phase-(N+1) lower bound), mandatory
  fields (REQ_F_CAP_004, REQ_F_BRK_004, REQ_O_003).
- **Covers:** REQ_F_CAP_001, REQ_F_CAP_004, REQ_F_BRK_004, REQ_O_003.

### 3.2 `models/` — L1
- **Purpose:** pure domain types; no I/O, no logic beyond validation.
- **Types (non-exhaustive):** `Money`, `Currency`, `Instrument`, `Stock`,
  `Turbo`, `StructuredProduct`, `Position`, `Order`, `Trade`, `Dividend`,
  `Account`, `Phase` (enum 1–6), `MarketRegime` (enum), `Injection`,
  `EquityPoint`, `KillSwitchState` (enum), `ImprovementReport`.
- **Covers:** scaffolds for all REQ_F_xxx, REQ_S_KS_xxx, REQ_F_MTO_xxx, etc.

### 3.3 `data/` — L2 — `MarketDataProvider`
- **Purpose:** abstract market-data access (historical and live).
- **Interface (`MarketDataProvider`):** `bars(instrument, timeframe, start, end)`,
  `latest(instrument)`, `dividends(instrument, year)`, `fundamentals(instrument)`.
- **Implementations:** `MockProvider` (synthetic series, deterministic seed),
  one or more concrete providers (broker-feed, public CSVs, etc.).
- **Covers:** REQ_F_BCT_001 (deterministic feed), REQ_F_SCR_001 (fundamentals),
  REQ_F_BCT_005 (dividends), REQ_NF_DET_001.

### 3.4 `execution/` — L2 — `BrokerAdapter`
- **Purpose:** abstract every broker operation; isolate broker-specific code.
- **Interface (`BrokerAdapter`):** `submit(order)`, `cancel(order_id)`,
  `positions()`, `account_state()`, `instrument(symbol)`, `subscribe(symbols)`.
- **Implementations:** `MockBrokerAdapter` (must support full end-to-end runs;
  REQ_F_BRK_002), `XTBAdapter` (XAPI; REQ_F_BRK_003).
- **Selection:** `config.broker.adapter` chooses the active implementation
  (REQ_F_BRK_004).
- **No upstream module** (L3+) imports a concrete broker; only `BrokerAdapter`
  (REQ_F_BRK_005).
- **Covers:** REQ_F_BRK_001 … REQ_F_BRK_005.

### 3.5 `tax/` — L3
- **Purpose:** France CTO (PFU) tax engine plus the tax-aware trade gate.
- **Inputs:** gross gain/dividend, fees, expected profit; tax rate from config.
- **Outputs:** net amount; boolean trade-validity verdict.
- **Operations:** `net_gain(gross)`, `net_dividend(gross)`, `trade_passes_gate(expected_net_profit, fees)`.
- **Phase 5+:** tax-loss harvester operates on realized PnL ledger.
- **Covers:** REQ_F_TAX_001, REQ_F_TAX_002, REQ_F_TAX_003, REQ_F_TAX_004,
  REQ_F_TAX_005, REQ_F_TAX_006, REQ_C_TAX_001.

### 3.6 `phase_engine/` — L3
- **Purpose:** decide the active phase from `equity + injected_capital` and
  expose the phase-specific constraints to all dependent modules.
- **State machine:** monotone upgrades, hysteresis on downgrades (configurable
  buffer, default 10% below the upper boundary of the lower phase).
- **Outputs:** `Phase`, plus a `PhaseConstraints` record (max positions,
  trades/month, allocation targets, turbo cap, risk-per-trade band, max DD).
- **Covers:** REQ_F_CAP_002, REQ_F_CAP_003, REQ_F_CAP_004, REQ_F_CAP_005,
  REQ_F_CAP_006, REQ_F_CAP_007, REQ_F_CAP_008, REQ_F_CAP_009, REQ_F_CAP_010,
  REQ_F_CAP_011, REQ_F_CAP_013.

### 3.7 `capital_flow/` — L3
- **Purpose:** track initial capital, every external injection, total deployed
  capital, equity. Provide the canonical "performance excluding injections"
  series.
- **Outputs:** `total_capital()`, `equity_excl_injections_curve()`,
  `injection_timeline()`.
- **Consumers:** `phase_engine/`, `risk/`, `backtesting/`, `analytics/`.
- **Covers:** REQ_F_CFL_001, REQ_F_CFL_002, REQ_F_CFL_003, REQ_F_CFL_004.

### 3.8 `screener/` — L3
- **Purpose:** EU dividend equity screener.
- **Pipeline:** universe → filter (yield 3–7%, payout < 70%, FCF > 0,
  D/E < 1.5, ≥ 5 y dividend history) → score (stability, yield quality,
  valuation) → ranked list.
- **Inputs:** fundamentals via `MarketDataProvider`.
- **Covers:** REQ_F_SCR_001, REQ_F_SCR_002.

### 3.9 `strategies/` — L4
- **Purpose:** generate trade candidates.
- **Interface:** `Strategy.evaluate(state) -> list[TradeProposal]`. State =
  market snapshot + portfolio + phase constraints.
- **Implementations:**
  - `CoreStrategy` — long-term holding, dividend compounding, low turnover
    (REQ_F_STR_001).
  - `TacticalStrategy` — trend following, breakout confirmation, pullback
    entries (REQ_F_STR_002).
  - `EnsembleStrategy` — Phase 6 multi-strategy harness with vol-targeting and
    risk-parity weights (REQ_F_STR_004).
- **Validation:** every shipped strategy carries a walk-forward validation
  certificate produced by `strategy_lab/` (REQ_F_STR_003).
- **Covers:** REQ_F_STR_001 … REQ_F_STR_004.

### 3.10 `turbo_selector/` — L3
- **Purpose:** select turbo candidates under hard rules.
- **Pipeline:** filter (knockout < 5%, spread > 1.5%, leverage above phase cap,
  low liquidity, extreme vol) → score (`0.35·KO + 0.25·LE + 0.20·cost +
  0.20·move`) → select; below-threshold → no trade.
- **Constraints:** risk = invested capital only; phase-capped exposure.
- **Inputs:** turbo universe via `MarketDataProvider`; current phase from
  `phase_engine/`.
- **Covers:** REQ_F_TRB_001, REQ_F_TRB_002, REQ_F_TRB_003, REQ_F_TRB_004,
  REQ_F_TRB_005, REQ_F_TRB_006.

### 3.11 `structured_products/` — L3
- **Purpose:** evaluate, gate, and size structured-product allocations.
- **Components:** `Classifier` (autocallable / barrier / capital-protected /
  leveraged certificate), `Decomposer` (equity-equiv, hidden leverage,
  worst-case loss, BE probability — non-decomposable products are rejected),
  `RegimeGate` (allowed: low-vol / sideways / stable-macro; forbidden:
  high-vol / crisis / liquidity stress), `IssuerDiversification`,
  `StressTester` (crash / vol expansion / correlation spike).
- **Allocation cap:** ≤ 10% portfolio; no stacking with turbos on the same
  underlying.
- **Covers:** REQ_F_STP_001, REQ_F_STP_002, REQ_F_STP_003, REQ_F_STP_004,
  REQ_F_STP_005, REQ_F_STP_006, REQ_F_STP_007.

### 3.12 `risk/` — L3
- **Purpose:** enforce all risk limits before a trade reaches execution.
- **Pre-trade checks:** per-trade size band (phase-dependent), single-asset
  exposure cap, correlation guard (with current portfolio), regime
  compatibility, stop-loss attached.
- **Post-trade checks (continuous):** drawdown, portfolio-level vol cap (Phase
  5+), correlation drift.
- **Failure mode:** any internal inconsistency → raise a kill-switch
  integrity trigger.
- **Covers:** REQ_F_RSK_001, REQ_F_RSK_002, REQ_F_RSK_003, REQ_F_RSK_004,
  REQ_F_RSK_005, REQ_F_CAP_012, REQ_F_CAP_014.

### 3.13 `safety/` — L3 (with veto over L2/L4) — Kill Switch
- **Purpose:** non-bypassable global override.
- **Components:**
  - `state_manager.py` — owns the `KillSwitchState` (ACTIVE / DEGRADED / KILL).
    Single writer; readers everywhere.
  - `monitor.py` — periodic evaluation of registered triggers.
  - `anomaly_detector.py` — feeds (financial, strategy, execution, integrity).
  - `kill_switch.py` — public façade: `must_halt() -> bool`,
    `current_state()`, `request_recovery(operator_token)`.
  - `alert_system.py` — operator notification on state transitions.
- **Override priority:** `KillSwitch > RiskEngine > Strategy > Execution`;
  encoded by mandatory `must_halt()` check in the execution path.
- **Recovery:** all-conditions-met **and** explicit operator confirmation
  (cryptographic token) — never auto-recovers.
- **Audit:** every state transition writes a full snapshot
  (positions, pending orders, equity, recent decisions) — REQ_NF_AUD_001.
- **Runtime immutability:** trigger thresholds load at startup and are not
  re-readable until restart (REQ_S_KS_010).
- **Covers:** REQ_S_KS_001, REQ_S_KS_002, REQ_S_KS_003, REQ_S_KS_004,
  REQ_S_KS_005, REQ_S_KS_006, REQ_S_KS_007, REQ_S_KS_008, REQ_S_KS_009,
  REQ_S_KS_010, REQ_S_KS_011, REQ_S_KS_012, REQ_NF_AUD_001.

### 3.14 `portfolio/` — L4
- **Purpose:** authoritative portfolio state.
- **State:** cash, positions, realized gains, dividends, **after-tax equity
  curve** (REQ_F_PRT_001 — the after-tax curve is the primary one; gross is a
  derived view, never the optimization target).
- **Phase 6 add-on:** NAV-style reporting + P&L attribution
  (REQ_F_PRT_002).
- **Diversification:** rejects new positions that would breach single-asset
  dominance limits (REQ_F_PRT_003).
- **Covers:** REQ_F_PRT_001, REQ_F_PRT_002, REQ_F_PRT_003.

### 3.15 `milestone_controller/` — L4
- **Purpose:** validate milestone crossings and unlock controlled scaling.
- **Inputs:** equity, capital flow, recent KS history, performance stability
  metrics.
- **Conditions to validate a milestone:** stable returns AND low drawdown AND
  strategy consistency AND no recent KS event.
- **Effect on success:** ≤ +10–20% exposure unlock (gradual, never
  exponential).
- **Fake-growth detector:** rejects scaling triggered by overfitting gains,
  vol-spike profits, or single-trade anomalies.
- **Covers:** REQ_F_MIL_001, REQ_F_MIL_002, REQ_F_MIL_003, REQ_F_MIL_004.

### 3.16 `backtesting/` — L5
- **Purpose:** deterministic simulation; the *only* sanctioned way to evaluate
  strategy performance.
- **Sub-components:**
  - `clock.py` — discrete event clock (no wall-clock dependency).
  - `market_replay.py` — reads from `MarketDataProvider`; supports synthetic
    regime stitching for stress runs.
  - `fee_model.py` — broker-parameterized fees & spreads.
  - `slippage_model.py`.
  - `knockout_simulator.py` — turbo barrier checks.
  - `dividend_simulator.py`.
  - `tax_apply.py` — calls `tax/` to convert gross to net at realization.
  - `injection_scheduler.py` — replays the explicit injection timeline.
  - `walk_forward.py` — train/validation/OOS harness; Phase 5+ uses extended
    windows covering multiple regime crossings.
- **Determinism:** seeded; same inputs → identical outputs (REQ_NF_DET_001).
- **Covers:** REQ_F_BCT_001, REQ_F_BCT_002, REQ_F_BCT_003, REQ_F_BCT_004,
  REQ_F_BCT_005, REQ_F_BCT_006, REQ_F_BCT_007, REQ_F_BCT_008, REQ_F_BCT_009,
  REQ_F_CFL_004, REQ_NF_DET_001, REQ_F_TAX_005.

### 3.17 `strategy_lab/` — L6 — Meta-Optimization
- **Purpose:** bounded research engine. Runs only offline; never executes
  trades (REQ_F_MTO_001, REQ_C_CLA_002).
- **8-step pipeline (REQ_F_MTO_002):**
  1. **`generator.py`** — Claude proposes N candidate variants
     (parameter / logic / filter / regime tweaks) under the rule
     *no structural risk increase* (REQ_C_CLA_001).
  2. **`backtester.py`** — wraps `backtesting/` for batch runs.
  3. **`evaluator.py`** — computes CAGR (net after tax), max DD, Sharpe,
     turnover, exposure profile, tail risk.
  4. **`risk_guard.py`** — hard rejection on DD breach, turnover excess,
     unstable cross-regime performance, leverage drift, parameter
     sensitivity (overfitting proxy).
  5. **walk-forward gate** — OOS collapse → reject (REQ_F_MTO_004).
  6. **scoring** — `0.4·net_after_tax + 0.3·sharpe + 0.2·stability +
     0.1·dd_penalty` (REQ_F_MTO_003).
  7. **`optimizer.py`** — pick top 1–3 only if they outperform baseline
     **and** do not raise risk profile; safe-self-improvement check:
     `new_risk ≤ baseline_risk` AND `new_return / new_risk > baseline`
     (REQ_F_MTO_006).
  8. **`registry.py`** — versioned, immutable storage of validated
     strategies; experimental ones flagged separately (REQ_F_MTO_005).
- **Output per cycle:** `ImprovementReport` (best id, deltas, risk
  assessment, rejected candidates, reasons) — REQ_F_MTO_007.
- **Regime evaluation:** strategies run across bull / bear / sideways /
  high-vol; failure in any → reject or downgrade (REQ_F_MTO_008).
- **Covers:** REQ_F_MTO_001 … REQ_F_MTO_008, REQ_C_CLA_001, REQ_C_CLA_002.

### 3.18 `analytics/` — L7
- **Purpose:** performance and monitoring computations.
- **Outputs:** equity curve (after-tax), drawdown series, exposure by class,
  attribution (Phase 6).
- **Logging:** trades, KS events, ImprovementReports — timestamped
  (REQ_NF_LOG_001).
- **Covers:** REQ_F_PRT_002, REQ_NF_LOG_001.

### 3.19 `dashboard/` — L7
- **Purpose:** display current phase, allocation, turbo exposure,
  after-tax performance, drawdown, trade history.
- **Implementation note (deferred to SDD):** read-only view of `analytics/`;
  no trading actions.
- **Covers:** REQ_F_DSH_001.

### 3.20 `main.py` — L7
- **Purpose:** end-to-end demo / runtime entry point. Connects (mock or
  selected `BrokerAdapter`) → runs screener → generates trades → applies
  phase logic → simulates portfolio → displays after-tax results.
- **Covers:** REQ_O_001, REQ_O_002, REQ_O_003.

---

## 4. External Interfaces

### 4.1 `BrokerAdapter`
| Method | Description | REQ |
|---|---|---|
| `submit(order)` | Submit an order; returns broker order id. | REQ_F_BRK_001 |
| `cancel(order_id)` | Cancel a pending order. | REQ_F_BRK_001 |
| `positions()` | Return all open positions. | REQ_F_BRK_001 |
| `account_state()` | Cash, equity, margin, realized/unrealized PnL. | REQ_F_BRK_001 |
| `instrument(symbol)` | Static metadata (turbo: knockout, leverage, spread). | REQ_F_BRK_001, REQ_F_TRB_006 |
| `subscribe(symbols)` | Stream market updates. | REQ_F_BRK_001 |

Selection at startup: `config.broker.adapter ∈ {"mock", "xtb", ...}` →
factory returns the matching implementation (REQ_F_BRK_002, REQ_F_BRK_003,
REQ_F_BRK_004).

### 4.2 `MarketDataProvider`
| Method | Description | REQ |
|---|---|---|
| `bars(...)` | OHLCV history. | REQ_F_BCT_001 |
| `latest(symbol)` | Most recent quote. | — |
| `dividends(symbol, year)` | Dividend events. | REQ_F_BCT_005, REQ_F_SCR_001 |
| `fundamentals(symbol)` | Yield, payout, FCF, D/E, dividend history. | REQ_F_SCR_001 |

### 4.3 `AlertChannel`
Operator notification on KS state transitions (log + push). Multiple channels
allowed (file, email, webhook).

### 4.4 `Config` (read-only at runtime)
Frozen dataclass produced by `config/`. All other modules accept it as a
constructor argument; nothing reads files directly.

---

## 5. Key Data Flows

### 5.1 Trade decision flow

```
MarketDataProvider ──► strategies/  ──┐
                                       ├─► tax-aware trade gate (tax/)
phase_engine/ ─────► PhaseConstraints ─┤
                                       ├─► risk/ pre-trade checks
portfolio/ ───────► portfolio state ───┤
                                       ├─► safety/ must_halt()
                                       ▼
                              execution/ BrokerAdapter
                                       │
                                       ▼
                              portfolio/ update
                                       │
                                       ▼
                              capital_flow/ update
                                       │
                                       ▼
                              analytics/ log + monitor
```

Every arrow into `execution/` is gated by `safety.must_halt()` and the
risk-engine verdict; either *halts* the flow (REQ_S_KS_011, REQ_F_RSK_005).

### 5.2 Phase resolution flow

```
capital_flow.total_capital() ──► phase_engine.resolve()
                                      │
                                      ▼
                              Phase + PhaseConstraints
                                      │
                       ┌──────────────┼─────────────────┐
                       ▼              ▼                 ▼
                    risk/         strategies/       turbo_selector/
                                                        │
                                                        ▼
                                               structured_products/
```

Hysteresis applied on downgrade (REQ_F_CAP_005).

### 5.3 Backtest flow

```
config/  ──► backtesting/ harness
              │     │     │      │       │       │
              │     │     │      │       │       └─► injection_scheduler
              │     │     │      │       └─► tax_apply (uses tax/)
              │     │     │      └─► dividend_simulator
              │     │     └─► knockout_simulator
              │     └─► slippage_model
              └─► fee_model
              │
              ▼
         simulated tick stream  ─►  same Trade Decision Flow as §5.1
              │                                (with mock adapters)
              ▼
         walk_forward harness  ─►  Train | Validation | OOS report
              │
              ▼
         deterministic equity curve (after tax) ─► analytics/
```

### 5.4 Meta-optimization flow

```
generator (Claude) ─► candidates ─► backtester ─► evaluator
                                                     │
                                                     ▼
                                                risk_guard ──► reject?
                                                     │             │
                                                     ▼             ▼
                                              walk-forward    rejection log
                                                     │
                                                     ▼
                                                 scoring
                                                     │
                                                     ▼
                                                 optimizer ─► safe-improvement check
                                                     │
                                                     ▼
                                                 registry (immutable)
                                                     │
                                                     ▼
                                            ImprovementReport
```

The runtime never imports from `strategy_lab/`; only `registry/` (read-only).
This is what keeps the meta-loop *bounded* (REQ_F_MTO_001).

### 5.5 Kill-switch flow

```
financial monitor    ─┐
strategy monitor      │
execution monitor     ├─► state_manager  ─►  KillSwitchState
integrity monitor     │         │
                      ┘         └─► alert_system + audit snapshot
                                          │
                                          ▼
            execution/.submit() must_halt()? if KILL → reject; if DEGRADED → derate
            strategies/.evaluate() must_halt()? if KILL → no proposals
            optimizer/.deploy() must_halt()? if KILL → no deployment
```

---

## 6. Configuration Model

| File | Purpose | Validates / enforces |
|---|---|---|
| `system.yaml` | Currency, starting capital, log level, broker selection, run mode (live / backtest / paper). | REQ_F_CAP_001, REQ_F_BRK_004, REQ_O_003 |
| `phases.yaml` | Phase boundaries, per-phase constraints (positions, trades, allocation, turbo cap, risk band, max DD, vol cap from Phase 5). | REQ_F_CAP_004 … REQ_F_CAP_013 |
| `risk.yaml` | Single-asset cap, correlation thresholds, vol-cap params, stop-loss policy. | REQ_F_RSK_001 … REQ_F_RSK_004 |
| `tax.yaml` | Tax rate (default 0.30), gate multiplier (default 5×). | REQ_F_TAX_001 … REQ_F_TAX_005, REQ_C_TAX_001 |
| `turbos.yaml` | Filter cutoffs and scoring weights. | REQ_F_TRB_002, REQ_F_TRB_003, REQ_F_TRB_004 |
| `structured.yaml` | Allocation cap, regime allow/deny lists, stress scenarios. | REQ_F_STP_001 … REQ_F_STP_007 |
| `kill_switch.yaml` | Trigger thresholds; loaded once at startup, immutable thereafter. | REQ_S_KS_003 … REQ_S_KS_010 |
| `meta_loop.yaml` | Candidate count, scoring weights, walk-forward windows, regime split. | REQ_F_MTO_002 … REQ_F_MTO_008 |

Schema validation runs at startup; any failure → fail-fast exit before any
decisioning module initializes.

---

## 7. Cross-Cutting Concerns

### 7.1 Determinism & reproducibility
- All randomness is seeded; the seed is part of `Config`.
- Strategy registry stores: code revision (git SHA), config hash, seed,
  resulting metrics. Replay produces identical numbers (REQ_NF_DET_001,
  REQ_NF_REP_001).

### 7.2 Logging & audit
- One structured log stream, JSON lines.
- Categories: `trade`, `decision`, `ks_event`, `phase_change`,
  `improvement_report`, `error`. Every record carries a timestamp and a
  correlation id (REQ_NF_LOG_001).
- KS state transitions also write a full snapshot artifact (REQ_NF_AUD_001).

### 7.3 Time
- Single `Clock` interface; `WallClock` for live, `EventClock` for backtests.
  No `datetime.now()` calls outside `data/` or `dashboard/`.

### 7.4 Behavioral defaults (constraints, not algorithms)
The system rejects rather than executes when in doubt (REQ_C_BHV_004,
REQ_S_KS_012). Phase 1–2 defaults are biased toward fee minimization and
low turnover (REQ_C_BHV_002). Stocks are preferred over turbos absent a
validated edge (REQ_C_BHV_001). Marginal trades fail the tax gate by
construction (REQ_C_BHV_003, REQ_F_TAX_003). Forbidden behaviors —
aggressive leverage scaling, KS bypass, "all-in" trades — are
unrepresentable in the API surface (REQ_C_BHV_005).

---

## 8. Design Requirements (SDS-level)

The SDS introduces design-level requirements (`REQ_SDS_*`) the SDD and code
must satisfy. Each is derived from one or more SRS requirements and refines
them with an explicit architectural decision. IDs are immutable after this
SDS is approved (per REQ_NF_LIF_002).

### 8.1 Architecture — `REQ_SDS_ARC`

- **REQ_SDS_ARC_001** — The system SHALL be organized into eight layers (L0–L7) with strict downward-only dependencies; higher layers MAY import from lower, lower MUST NOT import from higher. *Derives from: REQ_NF_TRC_001.* *V: I*
- **REQ_SDS_ARC_002** — Engines (tax, risk, phase, screener, turbo, scoring, backtest) SHALL be implemented as pure functions of inputs; all I/O SHALL live in adapters (data, execution). *Derives from: REQ_NF_DET_001, REQ_NF_REP_001.* *V: I*
- **REQ_SDS_ARC_003** — The safety layer SHALL act as a veto over execution; every call to `BrokerAdapter.submit()` SHALL be preceded by a `safety.must_halt()` check, and no module SHALL be able to bypass that check. *Derives from: REQ_S_KS_002, REQ_S_KS_011.* *V: I, T*
- **REQ_SDS_ARC_004** — The runtime SHALL be a single-process event-driven loop; the same loop logic SHALL execute in live and backtest modes (only the adapters differ). *Derives from: REQ_F_BCT_001.* *V: I, T*
- **REQ_SDS_ARC_005** — All randomness SHALL be seeded; the seed SHALL be part of `Config` so it is captured in registry entries. *Derives from: REQ_NF_DET_001, REQ_NF_REP_001.* *V: T*
- **REQ_SDS_ARC_006** — Time SHALL be abstracted via a `Clock` interface; no module outside `data/` and `dashboard/` SHALL call wall-clock primitives directly. *Derives from: REQ_NF_DET_001.* *V: I, T*

### 8.2 Module-level design decisions — `REQ_SDS_MOD`

- **REQ_SDS_MOD_001** — `config/` SHALL run schema validation at startup including cross-field invariants (e.g., adjacent phase boundary continuity); any failure SHALL be a fail-fast exit. *Derives from: REQ_F_CAP_004, REQ_O_003.* *V: T*
- **REQ_SDS_MOD_002** — `models/` SHALL contain only data types with input validation; it SHALL contain no I/O, no business logic, no module-level state. *Derives from: REQ_NF_TRC_001.* *V: I*
- **REQ_SDS_MOD_003** — The tax module's public functions (`net_gain`, `net_dividend`, `trade_passes_gate`) SHALL be pure; the tax rate SHALL be sourced exclusively from `tax.yaml`. *Derives from: REQ_F_TAX_001, REQ_F_TAX_002, REQ_F_TAX_003, REQ_C_TAX_001.* *V: T*
- **REQ_SDS_MOD_004** — The phase engine's state machine SHALL be monotone-up by default and SHALL apply a configurable hysteresis buffer (default ≥ 10%) before downgrading. *Derives from: REQ_F_CAP_002, REQ_F_CAP_005.* *V: T*
- **REQ_SDS_MOD_005** — `capital_flow/` SHALL expose an "equity excluding injections" series as the canonical performance series; downstream metrics SHALL consume this, not the raw equity. *Derives from: REQ_F_CFL_001, REQ_F_CFL_002.* *V: T*
- **REQ_SDS_MOD_006** — Every concrete strategy SHALL implement a single `evaluate(state) -> list[TradeProposal]` entry point; strategies SHALL NOT hold module-level state. *Derives from: REQ_F_STR_001, REQ_F_STR_002, REQ_F_STR_003.* *V: I, T*
- **REQ_SDS_MOD_007** — The turbo selector SHALL emit "no trade" when the best candidate's score is below the configured threshold; the threshold SHALL be configurable. *Derives from: REQ_F_TRB_004.* *V: T*
- **REQ_SDS_MOD_008** — Structured products that cannot be decomposed into equity-equiv exposure, hidden leverage, worst-case loss, and break-even probability SHALL be rejected at admission, before any allocation logic runs. *Derives from: REQ_F_STP_002.* *V: T*
- **REQ_SDS_MOD_009** — Risk-engine internal inconsistencies (e.g., contradictory limit evaluations) SHALL escalate to a kill-switch integrity trigger rather than fail silently. *Derives from: REQ_F_RSK_005, REQ_S_KS_006.* *V: T*
- **REQ_SDS_MOD_010** — `safety/state_manager` SHALL be the single writer for `KillSwitchState`; no other module SHALL hold a write reference. *Derives from: REQ_S_KS_001, REQ_S_KS_002, REQ_S_KS_010.* *V: I, T*
- **REQ_SDS_MOD_011** — The portfolio's primary equity curve SHALL be after-tax; any gross view SHALL be derived (computed, not stored as truth). *Derives from: REQ_F_PRT_001, REQ_F_TAX_004.* *V: T*
- **REQ_SDS_MOD_012** — The milestone controller SHALL include a fake-growth detector that rejects scaling driven by overfitting metrics, vol-spike profits, or single-trade anomalies. *Derives from: REQ_F_MIL_004.* *V: T*
- **REQ_SDS_MOD_013** — The backtesting engine SHALL accept an explicit seed and SHALL produce identical equity curves and trade logs for identical (seed, config, data) tuples. *Derives from: REQ_F_BCT_001, REQ_NF_DET_001, REQ_NF_REP_001.* *V: T*
- **REQ_SDS_MOD_014** — `strategy_lab/` SHALL be offline-only; runtime modules SHALL import only `strategy_lab/registry/` (read-only) — never the generator, optimizer, or backtester. *Derives from: REQ_F_MTO_001, REQ_C_CLA_002.* *V: I*
- **REQ_SDS_MOD_015** — `dashboard/` SHALL be a read-only view over `analytics/`; it SHALL NOT expose any trade-execution actions. *Derives from: REQ_F_DSH_001, REQ_C_CLA_002.* *V: I*

### 8.3 Interfaces — `REQ_SDS_INT`

- **REQ_SDS_INT_001** — The `BrokerAdapter` interface SHALL specify `submit`, `cancel`, `positions`, `account_state`, `instrument`, `subscribe` (per §4.1); concrete implementations SHALL conform via a shared conformance test suite. *Derives from: REQ_F_BRK_001, REQ_F_BRK_002, REQ_F_BRK_003.* *V: T*
- **REQ_SDS_INT_002** — The `MarketDataProvider` interface SHALL specify `bars`, `latest`, `dividends`, `fundamentals` (per §4.2); a `MockProvider` SHALL support deterministic synthetic series. *Derives from: REQ_F_BCT_001, REQ_F_SCR_001.* *V: T*
- **REQ_SDS_INT_003** — The `AlertChannel` interface SHALL deliver kill-switch state-change notifications to at least one configured channel; failure to deliver SHALL be retried and logged. *Derives from: REQ_S_KS_007.* *V: T*
- **REQ_SDS_INT_004** — `Config` SHALL be a frozen, dependency-injected object; no runtime module SHALL re-read configuration files after startup. *Derives from: REQ_F_CAP_001, REQ_F_CAP_004, REQ_S_KS_010.* *V: I, T*

### 8.4 Data flows — `REQ_SDS_FLO`

- **REQ_SDS_FLO_001** — Every trade decision SHALL traverse the order: tax-aware gate → risk pre-trade checks → phase constraints → kill-switch check, in that order, before reaching `BrokerAdapter.submit()`. *Derives from: REQ_F_TAX_003, REQ_F_RSK_001, REQ_F_CAP_002, REQ_S_KS_011.* *V: T*
- **REQ_SDS_FLO_002** — `phase_engine` SHALL distribute `PhaseConstraints` to `risk/`, `strategies/`, `turbo_selector/`, and `structured_products/` consistently within a single tick; downstream modules SHALL NOT cache stale constraints across phase transitions. *Derives from: REQ_F_CAP_002, REQ_F_CAP_005.* *V: T*
- **REQ_SDS_FLO_003** — Backtests SHALL use the same trade-decision pipeline as live runs, swapping only the adapters; this is verified by an integration test that runs identical strategy code against both modes. *Derives from: REQ_F_BCT_001, REQ_NF_DET_001.* *V: T*
- **REQ_SDS_FLO_004** — The runtime SHALL never invoke `strategy_lab/` modules other than the registry; meta-optimization runs SHALL be triggered by an out-of-band tool, not the runtime loop. *Derives from: REQ_F_MTO_001, REQ_C_CLA_002.* *V: I, T*
- **REQ_SDS_FLO_005** — Kill-switch monitors (financial, strategy, execution, integrity) SHALL feed the state manager via a single asynchronous channel; manual recovery SHALL require an explicit operator token validated at the channel boundary. *Derives from: REQ_S_KS_003, REQ_S_KS_004, REQ_S_KS_005, REQ_S_KS_006, REQ_S_KS_009.* *V: T*

### 8.5 Configuration — `REQ_SDS_CFG`

- **REQ_SDS_CFG_001** — Configuration SHALL be split across eight YAML files (`system`, `phases`, `risk`, `tax`, `turbos`, `structured`, `kill_switch`, `meta_loop`); no runtime parameter SHALL live outside this set. *Derives from: REQ_F_CAP_004, REQ_F_BRK_004.* *V: I*
- **REQ_SDS_CFG_002** — Schema validation SHALL run before any decisioning module initializes; on failure the process SHALL exit non-zero and SHALL NOT enter degraded mode. *Derives from: REQ_O_003.* *V: T*
- **REQ_SDS_CFG_003** — Kill-switch trigger thresholds SHALL be loaded once at startup and SHALL be unreachable to runtime mutation paths (no setters; immutable dataclass). *Derives from: REQ_S_KS_010.* *V: T*

### 8.6 Cross-cutting — `REQ_SDS_CRS`

- **REQ_SDS_CRS_001** — All logs SHALL be JSON-line records with categories (`trade`, `decision`, `ks_event`, `phase_change`, `improvement_report`, `error`) and a per-tick correlation id. *Derives from: REQ_NF_LOG_001.* *V: T*
- **REQ_SDS_CRS_002** — Every kill-switch state transition SHALL persist a snapshot artifact (positions, pending orders, equity, recent decisions) to a tamper-evident audit log. *Derives from: REQ_NF_AUD_001, REQ_S_KS_007.* *V: T*
- **REQ_SDS_CRS_003** — The strategy registry SHALL store, for every validated entry: code revision (git SHA), config hash, RNG seed, and metric vector; replay using the same triple SHALL produce bit-identical metrics. *Derives from: REQ_NF_REP_001, REQ_F_MTO_005.* *V: T*
- **REQ_SDS_CRS_004** — Behavioral defaults (preferring stocks, rejecting marginal trades, blocking KS bypass, forbidding "all-in" sizing) SHALL be encoded as unrepresentable in the API surface, not merely discouraged in documentation. *Derives from: REQ_C_BHV_001, REQ_C_BHV_003, REQ_C_BHV_005, REQ_S_KS_011.* *V: I*

---

## 9. SRS Coverage Summary

| SRS group | # reqs | Covered by SDS sections |
|---|---:|---|
| `REQ_F_CAP` | 14 | §3.1, §3.6, §6 |
| `REQ_F_CFL` | 4 | §3.7, §3.16, §6 |
| `REQ_F_TAX` | 6 | §3.5, §3.16, §6 |
| `REQ_F_BRK` | 5 | §3.4, §4.1, §6 |
| `REQ_F_SCR` | 2 | §3.8 |
| `REQ_F_STR` | 4 | §3.9 |
| `REQ_F_TRB` | 6 | §3.10 |
| `REQ_F_STP` | 7 | §3.11, §6 |
| `REQ_F_RSK` | 5 | §3.12, §6 |
| `REQ_F_BCT` | 9 | §3.16, §6 |
| `REQ_F_PRT` | 3 | §3.14 |
| `REQ_F_DSH` | 1 | §3.19 |
| `REQ_F_MIL` | 4 | §3.15 |
| `REQ_F_MTO` | 8 | §3.17, §5.4, §6 |
| `REQ_S_KS`  | 12 | §3.13, §5.5, §6 |
| `REQ_NF_*`  | 7 | §1.2, §3.13, §3.16, §7 |
| `REQ_C_*`   | 8 | §3.5, §3.17, §7.4 |
| `REQ_O_*`   | 3 | §3.20, §6 |

Verification: run `python tools/traceability.py --report` after approval —
every SRS requirement (108) SHALL be referenced from `docs/sds.md`, and the
SDS itself contributes 37 design-level requirements (`REQ_SDS_*` defined in
§8) for a total of 145 tracked items. Coverage summary at this gate:
`reached SDS: 100%` (every requirement has at least an SDS landing zone).

---

## 10. Approval

This document is **DRAFT**. The Phase 2 → Phase 3 (SDD) gate is not opened
until the operator reviews and approves this SDS against SRS traceability.

| Date       | Reviewer      | Revision (git SHA) | Outcome  |
|------------|---------------|--------------------|----------|
| _pending_  | _pending_     | _pending_          | _pending_ |
