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

**Broker is abstracted** behind a `BrokerAdapter` interface. The lifecycle
ships a single concrete adapter — `LocalBrokerAdapter` — which is an
in-process deterministic broker that simulates fills, fees, and slippage.
**Live-broker adapters are deferred** until a broker is selected; when a
broker is chosen, the corresponding adapter goes through the full lifecycle
(SRS amendment → SDS → SDD → Test Plan → implementation) and must pass the
same conformance suite as `LocalBrokerAdapter`. The rest of the system
must not depend on any concrete broker.

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
8. **Every task ends with a documentation update.** A task is not complete until
   the documentation reflects the change. Before claiming a task done:
   - **`tasks.md`** — check the matching box, append `✅ DONE <YYYY-MM-DD> @ <SHA>`
     for phase steps, or add a one-line note for ad-hoc work.
   - **`docs/traceability.csv`** — re-run `python3 tools/traceability.py` and stage
     the regenerated CSV in the same commit as the code change. The status bar
     (`reached TP / CODE / TEST`) goes in the commit body.
   - **Wiki documents** (`Documentations/SRS.md`, `SDS.md`, `SDD.md`,
     `Test-Plan.md`) — if a design decision was made or refined during the work,
     amend the corresponding wiki page and add a re-approval row to its approval
     table per `REQ_NF_LIF_002`. Bump the `Documentations/` submodule pointer in
     the same main-repo commit (or a follow-up).
   - **`CLAUDE.md`** — update if hard rules, conventions, module layout, or
     workflow changed.
   - **`README.md`** — update if user-facing status changed (a phase completing,
     a license change, a structural shift).

   Documentation drift is a defect; the traceability tool's `--check` mode is the
   CI gate that catches it for the matrix, but the wider rule applies to every
   artifact listed above.
9. **Option / Result, not exceptions.** Following Rust's discipline:
   - Fallible operations return `Result[T, E]` (`Ok(value)` | `Err(error)`).
   - Possibly-absent values return `Option[T]` (`Some(value)` | `Nothing()`).
   - `try`/`except` is forbidden for control flow at module boundaries;
     `raise` is reserved for **panics** — programmer-error invariants
     ("this can never happen") via `assert` or `RuntimeError` only.
   - At third-party boundaries that *do* raise (file I/O, JSON parsing,
     network), wrap the call once at the adapter and return a `Result`.
   - Pattern-match on the union (`match res: case Ok(v): ... case Err(e): ...`),
     never `try: x.unwrap()`. `unwrap()` is permitted only when the call
     site has already proved the variant.

## Coding conventions

### Error handling — `Option[T]` and `Result[T, E]`
Implemented in `trading_system/result.py` (stdlib only, frozen dataclasses).
The two unions are:

```python
Result[T, E] = Ok[T] | Err[E]
Option[T]    = Some[T] | Nothing
```

Methods that the implementation MUST provide on both unions:
`is_ok` / `is_err` (resp. `is_some` / `is_none`), `map`, `and_then`,
`unwrap_or`, `unwrap_or_else`, `unwrap` (panics on the wrong variant —
use only when the variant is statically known).

Adapter modules (`execution/`, `data/`) wrap third-party exceptions:

```python
def submit(self, order: Order) -> Result[OrderId, BrokerError]:
    try:
        oid = self._client.submit(order.to_wire())
    except SomeBrokerLib.RejectedError as e:
        return Err(BrokerError("broker:rejected", str(e)))
    except SomeBrokerLib.NetworkError as e:
        return Err(BrokerError("network:timeout", str(e)))
    return Ok(OrderId(oid))
```

Engine modules (`tax/`, `risk/`, `phase_engine/`, etc.) never see those
exceptions; they consume `Result` and propagate via `and_then`.

`raise` in production code is reserved for two cases only:
1. `assert` / `RuntimeError` for programmer-error invariants (panic).
2. Type-construction validators where the input is *already known* to
   come from trusted internal code (still rare — prefer a `try_new`
   classmethod returning `Result`).

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

models → data → tax → broker adapter (`LocalBrokerAdapter`) → phase_engine → screener →
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

### Change Requests
Proposed evolutions that have **not yet entered the lifecycle** live in
`Documentations/Change-Requests.md`. The approved artifacts (SRS, SDS,
SDD, Test Plan) are locked at specific commits per `REQ_NF_LIF_002`;
when a CR is accepted, the change cascades through the full lifecycle
(SRS amendment → SDS → SDD → Test Plan → code).

CR rules:
- New feature ideas / refactors with non-trivial design impact go into
  the CR log first, **never directly into an approved spec**.
- CR ids are immutable; rejected CRs keep their numbers.
- Acceptance opens an SRS amendment that re-cascades all four phases.
- Each CR carries a status (Proposed / Accepted / In-Progress / Done /
  Deferred / Rejected), the affected artifacts, open questions, and a
  discussion log.

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

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
