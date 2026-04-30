# trading-bot

Production-grade Python trading system optimizing **after-tax** returns under
French CTO / PFU taxation (30 % flat). Manages EU dividend & swing stocks,
tactical positions, and turbo / CFD leveraged instruments — scaling capital
through six gated phases, from a configurable starting amount up to
multi-million portfolios. **Broker-agnostic** by design; XTB (XAPI) ships
as the reference adapter.

The repository is structured for engineering discipline first
(DO-178C-inspired lifecycle, full requirement traceability) and trading
performance second. It is a personal-use system, not financial advice or a
redistributable product.

---

## Project status

Lifecycle phases (gated; each is reviewed and approved before the next opens):

| Phase | Artifact | Status |
|---|---|---|
| 1 — Software Requirements (SRS) | [Wiki › SRS](https://github.com/lfazio/trading-bot/wiki/SRS) | Approved at `7424909` |
| 2 — System Design (SDS) | [Wiki › SDS](https://github.com/lfazio/trading-bot/wiki/SDS) | Approved at `26ce913` |
| 3 — Detailed Design (SDD) | [Wiki › SDD](https://github.com/lfazio/trading-bot/wiki/SDD) | Approved at `9ee11d5` |
| 4 — Test Plan | [Wiki › Test-Plan](https://github.com/lfazio/trading-bot/wiki/Test-Plan) | Draft |
| 5 — Implementation | `trading_system/` | Pending Phase 4 approval |
| 6 — Test Execution | `tests/` | — |
| 7 — Validation & Traceability | [`docs/traceability.csv`](./docs/traceability.csv) | — |

**234 requirements** are tracked across SRS / SDS / SDD / Test Plan
(108 + 37 + 72 + 17). Current coverage: `reached TP: 100 %`. Run
`python3 tools/traceability.py --report` for the live snapshot.

---

## Repository layout

```
trading-bot/
├── Documentations/          ← submodule → trading-bot.wiki.git
│   ├── Home.md              ← wiki landing page
│   ├── SRS.md               ← Software Requirements Specification
│   ├── SDS.md               ← System Design Specification
│   ├── SDD.md               ← Software Design Description
│   └── Test-Plan.md         ← Test Plan
├── docs/
│   └── traceability.csv     ← REQ ↔ artifact matrix (generated)
├── tools/
│   └── traceability.py      ← REQ scanner / drift gate
├── trading-bot.md           ← original imported specification
├── tasks.md                 ← engineering work breakdown
├── CLAUDE.md                ← guidance for Claude Code agents
└── README.md                ← this file
```

`trading_system/` (production code) and `tests/` arrive in Phase 5, after
the Test Plan is approved.

---

## Getting started

### Clone with the wiki submodule

```bash
git clone --recurse-submodules git@github.com:lfazio/trading-bot.git
cd trading-bot

# or, after a plain clone:
git submodule update --init --recursive
```

### Inspect the design

The lifecycle documents live in the wiki — browse them on GitHub or
locally under `Documentations/`. The
[Wiki Home page](https://github.com/lfazio/trading-bot/wiki) is the
recommended entry point.

### Verify traceability

```bash
python3 tools/traceability.py --report          # human-readable summary
python3 tools/traceability.py --check           # CI gate; exit 1 on drift
```

### Edit documentation

Edit pages in `Documentations/` as ordinary markdown. The submodule is a
separate git repo; commits there go to the wiki, not the main repo.

```bash
cd Documentations
# edit SRS.md / SDS.md / SDD.md / Test-Plan.md / Home.md
git add . && git commit -m "Refine REQ_F_TAX_006 wording"
git push origin master

cd ..
git add Documentations && git commit -m "Bump wiki to <sha>"
git push
```

---

## Hard rules

From the SRS and approved design documents — non-negotiable, apply to every
phase:

1. **Optimize net after-tax return.** Never gross. Backtests apply the
   30 % CTO / PFU tax and simulate the capital-injection timeline.
2. **Tax-aware trade gate.** A trade is valid only if
   `expected_net_profit > 5 × total_fees` *after tax*.
3. **Phase-gated lifecycle.** SRS → SDS → SDD → Test Plan → Code →
   Tests → Validation. No phase skipped; each is reviewed and approved
   before the next opens. Any change after approval restarts the
   lifecycle from the affected phase.
4. **Kill switch is non-bypassable.** Priority:
   `KillSwitch > RiskEngine > Strategy > Execution`. Trading stops on
   trip; recovery requires explicit operator confirmation.
5. **Bounded research engine.** The meta-optimization loop runs offline;
   the runtime imports only the read-only strategy registry.
6. **Configurable, never hard-coded.** Starting capital, broker
   selection, phase thresholds, risk limits — all from `config/*.yaml`.
7. **Survival > return.** Stopping incorrectly is preferred to trading
   incorrectly.

The full ruleset, including agent-specific guidance, is in
[`CLAUDE.md`](./CLAUDE.md).

---

## Contributing

Changes follow the lifecycle:

- **Spec or design changes** edit the relevant page in
  `Documentations/` and require re-approval (a new commit appended to
  the document's *Approval* section, with date, reviewer, and main-repo
  SHA).
- **Code changes** (Phase 5 onward) reference the requirement IDs they
  implement in module docstrings; tests reference the requirement IDs
  they verify. The traceability tool's `--check` mode runs in CI and
  blocks merges on drift, unknown REQ references, or duplicate
  definitions.
- **Tasks / tickets** live as
  [GitHub Issues](https://github.com/lfazio/trading-bot/issues) — one
  parent issue per phase, child issues per module or bug, labels
  `phase-N` / `module-X` / `bug` / `enhancement`.

---

## License

Released under the BSD 3-Clause License — see [`LICENSE`](./LICENSE) for
the full text. The license applies to all files in this repository and
to the documentation in the `Documentations/` submodule.
