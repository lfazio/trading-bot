# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A production-grade Python trading system optimizing **after-tax** returns under
France CTO taxation (30% flat on realized gains and dividends). It manages EU
dividend/swing stocks, tactical positions, and turbo/CFD leveraged instruments,
scaling capital through gated phases.

**Starting capital is configurable** (set in `config/`, not hardcoded). The phase
engine determines the active phase from current equity + injected capital — there is
no privileged starting amount.

**Broker is abstracted** behind a `BrokerAdapter` interface. XTB (XAPI) is the
reference adapter and the one shipped first, but any adapter implementing the
interface (orders, positions, leveraged instruments, market data) is acceptable. The
rest of the system must not depend on broker-specific details.

The full specification lives in [`trading-bot.md`](./trading-bot.md) — note that the
imported spec names XTB and 1000€ explicitly; this CLAUDE.md generalizes those. The
work breakdown is in [`tasks.md`](./tasks.md). Both derived files are authoritative
for engineering decisions — read them before making non-trivial changes.

## Hard rules — do not violate

1. **DO-178C-inspired lifecycle is gated.** Phases run SRS → SDS → SDD → Test Plan →
   Implementation → Test Execution → Validation. Do not write production code before
   the corresponding design document exists and has been approved. Any change
   restarts the lifecycle from the affected phase.
2. **Every requirement must be traceable** REQ → SDS → SDD → code → test. Update
   `docs/traceability.csv` (or equivalent) when adding modules or tests.
3. **After-tax optimization only.** Never optimize gross return. All backtests must
   apply the 30% France CTO tax and simulate the capital-injection timeline.
4. **Tax-aware trade gate.** A trade is valid only if
   `expected_net_profit > 5 × total_fees` *after tax*. Reject otherwise.
5. **Kill switch is non-bypassable.** Priority order:
   `KillSwitch > RiskEngine > Strategy > Execution`. No module may trade while the
   kill switch is tripped, and runtime modification of kill switch conditions is
   forbidden.
6. **Claude's role is bounded.** You may generate strategy candidates, refactor
   logic, propose filters/regime detection, and explain failures. You **must not**
   simulate results yourself, bypass risk constraints, or override the deterministic
   backtest engine — Python code does the simulation, not the model.
7. **Safe self-improvement.** A new strategy is accepted only if
   `new_risk ≤ baseline_risk` AND `new_return/risk > baseline`. Otherwise discard.

## Phase scaling (capital-driven)

Thresholds are defaults; they live in `config/phases.yaml` and may be tuned per
deployment. The phase engine selects a phase from `equity + injected_capital`.

| Phase | Capital range | Max positions | Trades/mo | Turbos | Max DD | Risk/trade |
|---|---|---|---|---|---|---|
| 1 — Capital Builder       | up to 3 000 €            | 3   | 4    | disabled                      | 15% | 1–2%   |
| 2 — Stability             | 3 000 – 10 000 €         | 6   | 8    | 1 pos, ≤ 5% exposure          | 15% | 1–2%   |
| 3 — Systematic            | 10 000 – 50 000 €        | 12  | 20   | enabled, 10–15% exposure      | 20% | 1–2%   |
| 4 — Capital Acceleration  | 50 000 – 200 000 €       | 20+ | 40+  | ≤ 20% exposure, hedging       | 20% | 1–1.5% |
| 5 — Wealth Preservation   | 200 000 – 1 000 000 €    | 30+ | 60+  | ≤ 15% exposure, hedging req.  | 15% | 0.5–1% |
| 6 — Scale / Institutional | > 1 000 000 €            | 50+ | 100+ | ≤ 10% exposure, hedge overlay | 12% | 0.25–0.75% |

**Phase 5 — Wealth Preservation.** Tighter drawdown and per-trade risk; capital is
large enough that absolute losses dominate over percentage gains. Allocation skews
to lower-vol core (≈55% dividend aristocrats / quality), 15% tactical, 15% structured
products, 10% turbos, 5% cash/hedges. Adds: tax-loss harvesting, sector rotation,
currency hedging on non-EUR exposure.

**Phase 6 — Scale / Institutional.** Multi-strategy ensemble with strict
vol-targeting and risk parity. Allocation ≈60% diversified core (multi-region),
15% tactical, 10% structured, 10% turbos, 5% alternatives/cash. Mandatory hedging
overlay; full attribution and NAV-style reporting. New strategies enter only via
the meta-optimization loop with extended walk-forward windows.

Stop-loss is mandatory in every phase. Phase 5+ also require a portfolio-level
volatility cap, not just per-trade limits.

## Module layout (target)

```
trading_system/
├── config/  data/  models/  screener/  strategies/  risk/  tax/
├── backtesting/  portfolio/  execution/  phase_engine/  turbo_selector/
├── dashboard/  safety/  strategy_lab/  milestone_controller/
├── structured_products/  capital_flow/  analytics/
└── main.py
```

`safety/` contains the kill switch (`kill_switch.py`, `monitor.py`,
`anomaly_detector.py`, `state_manager.py`, `alert_system.py`). `strategy_lab/` is the
bounded research engine (generator → backtester → evaluator → risk_guard → optimizer
→ registry → loop_controller).

## Implementation order (mandatory)

models → data → tax → broker adapter (XTB first) → phase_engine → screener →
strategy engine → turbo selector → risk engine → backtesting → portfolio → dashboard.
Cross-cutting modules (`safety/`, `strategy_lab/`, `milestone_controller/`,
`structured_products/`, `capital_flow/`, `analytics/`) are built alongside.

## Backtest engine non-negotiables

Must simulate: broker fees (spreads + commissions, parameterized per adapter),
slippage, turbo knockouts, dividends, **30% CTO tax**, and the explicit
external-capital-injection timeline. Performance metrics must exclude injections.
Walk-forward (train / validation / out-of-sample) is required for every strategy
candidate; collapse out-of-sample → reject. Phase 5+ require extended walk-forward
windows (longer history, multiple regime crossings).

## Turbo selection (when enabled)

1. **Filter:** reject if knockout distance < 5%, spread > 1.5%, leverage too high
   for the phase, low liquidity, or extreme volatility.
2. **Score:** `0.35·knockout_distance + 0.25·leverage_efficiency + 0.20·cost + 0.20·expected_move_capture`.
3. **Select:** rank, pick best; if score < threshold → no trade.

## Structured products (income overlay)

Optional, capped at **10%** of total portfolio. Allowed only in low-vol / sideways /
stable-macro regimes. Every product must be decomposable into equity-equivalent
exposure, hidden leverage, worst-case loss, and break-even probability — otherwise
**reject**. Never stack with turbos on the same underlying.

## Meta-optimization scoring

`score = 0.4·net_return_after_tax + 0.3·sharpe + 0.2·stability + 0.1·drawdown_penalty`.
Each cycle emits an `ImprovementReport` with best strategy, deltas, risk assessment,
and rejection reasons. Accepted strategies are versioned and immutable in the
registry; experimental ones are flagged.

## Behavioral defaults

- Prefer stocks over turbos unless edge is strong.
- Avoid overtrading, especially in early phases — fee minimization matters.
- Reject marginal trades automatically.
- Survival > return. Stopping incorrectly > trading incorrectly.

## Repository workflow (GitHub)

This repository is hosted on GitHub. Three GitHub primitives are used in
addition to the local design docs:

### Origin
The canonical remote is `origin` on GitHub. The operator configures it once:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

Claude **must not** configure remotes, force-push, push to `main`, or run
any other publishing command without explicit per-action operator
confirmation (this is a hard-to-reverse, shared-state action — see
"Executing actions with care" in the system instructions).

### Tasks / tickets — GitHub Issues
Work is tracked as GitHub Issues alongside `tasks.md`:

- One **parent issue per lifecycle phase** ("Phase 5 — Implementation",
  "Phase 6 — Test Execution", …) with a checkbox sublist for the
  module-level tasks already enumerated in `tasks.md`.
- **Module / feature work** uses child issues linked back to the parent;
  tag with labels `phase-N`, `module-<name>`, `enhancement`.
- **Bugs** use the `bug` label and reference any failing TC ids
  (e.g., `TC_TAX_004`) and REQ ids (e.g., `REQ_F_TAX_003`) so
  traceability survives across the issue tracker.
- `tasks.md` stays the authoritative engineering plan; issues are the
  *operational* tracker. The two must not drift — every issue references
  the relevant section in `tasks.md`, and closing a task in `tasks.md`
  closes its issue.
- CLI: use `gh issue create / list / view / edit`. Do not create, edit,
  or close issues without operator confirmation; do not auto-close on
  commit messages without explicit instruction.

### Documentation — GitHub Wiki (submodule)
The lifecycle documents live **inside the wiki submodule**, not in the
main repo. The wiki repo (`<repo>.wiki.git`) is mounted at
`Documentations/` via `.gitmodules`:

```
trading-bot/
├── Documentations/         ← submodule → trading-bot.wiki.git
│   ├── Home.md             ← wiki landing page
│   ├── SRS.md              ← Software Requirements Specification
│   ├── SDS.md              ← System Design Specification
│   ├── SDD.md              ← Software Design Description
│   └── Test-Plan.md        ← Test Plan
├── docs/
│   └── traceability.csv    ← build artifact (regenerated)
├── tools/traceability.py   ← reads from Documentations/, writes to docs/
└── ...
```

`Documentations/` is a separate git repo with its own `master` branch
(GitHub wiki convention) and its own commit history. The main repo
records a **gitlink** (160000 mode) pointing to a specific commit in
the wiki repo; advancing the wiki updates that pointer.

**Initial bootstrap** (one-time, by operator):
1. Visit `https://github.com/<owner>/<repo>/wiki` and create one wiki
   page in the GitHub UI — any title and content. This provisions the
   `<repo>.wiki.git` repository on GitHub's side.
2. From `Documentations/`, force-push the local content to overwrite
   the placeholder page:
   ```bash
   cd Documentations
   git push --force-with-lease origin master
   ```
3. From the main repo, push to GitHub:
   ```bash
   git push -u origin main
   ```

**Daily workflow** (editing docs):
1. Edit pages in `Documentations/` as a normal markdown file.
2. Commit inside the submodule:
   ```bash
   cd Documentations && git add . && git commit -m "..." && git push
   ```
3. Update the submodule pointer in the main repo:
   ```bash
   cd .. && git add Documentations && git commit -m "Bump wiki to <sha>"
   ```

**Cloning the project** (fresh checkout):
```bash
git clone --recurse-submodules git@github.com:<owner>/<repo>.git
# or, after a plain clone:
git submodule update --init --recursive
```

**Cross-references inside wiki pages** use the GitHub wiki link form
`[Title](Page-Name)` (e.g., `[SRS](SRS)`, not `./srs.md`). Links from
wiki pages to files in the main repo use absolute GitHub blob URLs
(e.g., `https://github.com/<owner>/<repo>/blob/main/CLAUDE.md`).

**Tooling** — `tools/traceability.py` reads `Documentations/SRS.md`,
`Documentations/SDS.md`, `Documentations/SDD.md`, and
`Documentations/Test-Plan.md` by default; CSV output stays in
`docs/traceability.csv` as a build artifact (regenerated, do not edit
by hand).

### Safety rules for GitHub operations
- No `git push`, `gh issue create`, `gh pr create`, wiki commits, or
  release tags without explicit per-action operator confirmation.
- `tools/traceability.py --check` is read-only and safe to run anytime.
- Approval entries (Section *Approval* in each design doc) are recorded
  as **new commits**, never as amends, so the lifecycle history is
  immutable on `main`.

## Notes on the spec file

`trading-bot.md` is a Google Docs markdown export and is the **original imported
spec** — it names XTB and 1000€ explicitly. This CLAUDE.md and `tasks.md`
generalize those: broker is abstracted, starting capital is configurable. Treat
`trading-bot.md` as historical/reference; engineering decisions follow the derived
files.

Other artifacts in the spec:
- Many backslash-escaped chars (`\+`, `\#`, `\<`, `\>`, etc.) are export noise, not literals.
- The heading `# ETF + calable` is a typo; the section is the structured-products spec.
- The spec ships several `# END OF SPEC` markers — they delimit logical sub-specs
  within one document, not multiple documents.
