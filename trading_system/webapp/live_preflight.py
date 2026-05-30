"""CR-019 step 2 — Operator pre-flight gate (REQ_F_LIV_005 / REQ_SDD_LIV_004).

Six gates run in the documented order; short-circuit on the first
failure. The output JSON artefact (default `var/live-preflight.json`)
is the durable signal the webapp's dashboard reads to decide whether
the `live` mode-switch chip is enabled (REQ_F_LIV_002).

REQ refs: REQ_F_LIV_002, REQ_F_LIV_005, REQ_SDD_LIV_004, REQ_NF_LIV_001.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from trading_system.observability import structured_log


_LOGGER = logging.getLogger(__name__)


# Gate name constants — locked so test assertions don't drift.
GATE_BROKER_SELECTOR = "broker_selector"
GATE_BROKER_AUTHENTICATE = "broker_authenticate"
GATE_OPERATOR_TOKEN = "operator_token"
GATE_KILL_SWITCH = "kill_switch"
GATE_PERSISTENCE_INTEGRITY = "persistence_integrity"
GATE_MARKET_DATA = "market_data"

# Documented order — REQ_SDD_LIV_004.
GATES_IN_ORDER: tuple[str, ...] = (
    GATE_BROKER_SELECTOR,
    GATE_BROKER_AUTHENTICATE,
    GATE_OPERATOR_TOKEN,
    GATE_KILL_SWITCH,
    GATE_PERSISTENCE_INTEGRITY,
    GATE_MARKET_DATA,
)


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One gate's verdict."""

    name: str
    outcome: str  # "ok" / "failed" / "skipped"
    message: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in ("ok", "failed", "skipped"):
            raise ValueError(
                f"GateOutcome.outcome must be one of "
                "{'ok', 'failed', 'skipped'}, "
                f"got {self.outcome!r}"
            )


@dataclass(slots=True)
class PreflightReport:
    """The output artefact shape (REQ_SDD_LIV_004)."""

    checked_at: datetime
    outcome: str  # "ok" / "failed"
    gates: list[GateOutcome] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at.isoformat(),
            "outcome": self.outcome,
            "gates": [
                {
                    "name": g.name,
                    "outcome": g.outcome,
                    "message": g.message,
                }
                for g in self.gates
            ],
        }


# ---------------------------------------------------------------------------
# Gate Protocols — each gate is a small callable returning a
# GateOutcome. The CLI wires the real implementations; tests inject
# stubs.
# ---------------------------------------------------------------------------


def gate_broker_selector(*, system_config) -> GateOutcome:
    """REQ_F_LIV_005 (a) — broker.adapter SHALL NOT be 'local'.

    CR-025 (REQ_F_PAP_014): ``"paper"`` is accepted alongside any
    concrete live-broker selector (REQ_F_BRK_003 family). Only
    ``"local"`` is rejected — that's the deterministic in-process
    backtest baseline, never live."""
    selector = getattr(system_config.broker_adapter, "lower", lambda: "")()
    if not selector or selector == "local":
        return GateOutcome(
            name=GATE_BROKER_SELECTOR,
            outcome="failed",
            message=(
                f"broker.adapter is {selector!r}; live mode requires a "
                "concrete live adapter (REQ_F_BRK_003) or 'paper' "
                "(REQ_F_PAP_013)."
            ),
        )
    return GateOutcome(
        name=GATE_BROKER_SELECTOR,
        outcome="ok",
        message=f"broker.adapter={selector!r}",
    )


def gate_broker_authenticate(*, broker) -> GateOutcome:
    """REQ_F_LIV_005 (b) — adapter reaches account_state() Ok."""
    try:
        state = broker.account_state()
    except Exception as e:  # noqa: BLE001 — preflight is the boundary
        return GateOutcome(
            name=GATE_BROKER_AUTHENTICATE,
            outcome="failed",
            message=f"account_state raised {type(e).__name__}: {e}",
        )
    if state is None:
        return GateOutcome(
            name=GATE_BROKER_AUTHENTICATE,
            outcome="failed",
            message="account_state returned None",
        )
    return GateOutcome(
        name=GATE_BROKER_AUTHENTICATE,
        outcome="ok",
        message="adapter authenticated",
    )


def gate_operator_token(*, secret_env_var: str = "TRADING_BOT_OPERATOR_SECRET") -> GateOutcome:
    """REQ_F_LIV_005 (c) — operator-token secret provisioned."""
    secret = os.environ.get(secret_env_var)
    if not secret:
        return GateOutcome(
            name=GATE_OPERATOR_TOKEN,
            outcome="failed",
            message=f"env var {secret_env_var!r} is not set",
        )
    return GateOutcome(
        name=GATE_OPERATOR_TOKEN,
        outcome="ok",
        message=f"env var {secret_env_var!r} is set",
    )


def gate_kill_switch(*, ks_state) -> GateOutcome:
    """REQ_F_LIV_005 (d) — kill switch SHALL be ACTIVE."""
    state_value = getattr(ks_state, "value", str(ks_state)).upper()
    if state_value != "ACTIVE":
        return GateOutcome(
            name=GATE_KILL_SWITCH,
            outcome="failed",
            message=f"kill switch is {state_value} (must be ACTIVE)",
        )
    return GateOutcome(
        name=GATE_KILL_SWITCH,
        outcome="ok",
        message="kill switch state is ACTIVE",
    )


def gate_persistence_integrity(*, conn) -> GateOutcome:
    """REQ_F_LIV_005 (e) — `PRAGMA integrity_check` == 'ok'."""
    try:
        result = conn.pragma("integrity_check")
    except Exception as e:  # noqa: BLE001 — preflight boundary
        return GateOutcome(
            name=GATE_PERSISTENCE_INTEGRITY,
            outcome="failed",
            message=f"integrity_check raised: {e}",
        )
    if result != "ok":
        return GateOutcome(
            name=GATE_PERSISTENCE_INTEGRITY,
            outcome="failed",
            message=f"integrity_check returned {result!r}",
        )
    return GateOutcome(
        name=GATE_PERSISTENCE_INTEGRITY,
        outcome="ok",
        message="WAL integrity ok",
    )


def gate_market_data(*, market_data_provider, instruments) -> GateOutcome:
    """REQ_F_LIV_005 (f) — provider.latest() returns Ok for every
    instrument in the active universe."""
    failed: list[str] = []
    for instrument in instruments:
        try:
            result = market_data_provider.latest(instrument)
        except Exception as e:  # noqa: BLE001 — preflight boundary
            failed.append(f"{instrument.id}: {type(e).__name__}: {e}")
            continue
        if hasattr(result, "is_ok") and not result.is_ok():
            failed.append(f"{instrument.id}: {result.error}")
    if failed:
        return GateOutcome(
            name=GATE_MARKET_DATA,
            outcome="failed",
            message="; ".join(failed[:3]) + (
                f" (+{len(failed) - 3} more)" if len(failed) > 3 else ""
            ),
        )
    return GateOutcome(
        name=GATE_MARKET_DATA,
        outcome="ok",
        message=f"latest() ok for {len(instruments)} instrument(s)",
    )


# ---------------------------------------------------------------------------
# Runner — composes the gates in REQ_SDD_LIV_004 order
# ---------------------------------------------------------------------------


def run_preflight(
    *,
    system_config,
    broker,
    conn,
    ks_state,
    market_data_provider,
    instruments,
    secret_env_var: str = "TRADING_BOT_OPERATOR_SECRET",
    now: datetime | None = None,
) -> PreflightReport:
    """Run the six gates in order; short-circuit on the first
    failure. Every gate emits a structured-log entry under
    `category="system"` (REQ_F_LIV_005)."""
    when = now or datetime.now(tz=UTC)
    report = PreflightReport(checked_at=when, outcome="ok", gates=[])

    # Run the gates in order; on failure, mark every remaining
    # gate as "skipped" so the operator can see exactly where the
    # short-circuit happened.
    gate_callables = [
        (GATE_BROKER_SELECTOR, lambda: gate_broker_selector(system_config=system_config)),
        (GATE_BROKER_AUTHENTICATE, lambda: gate_broker_authenticate(broker=broker)),
        (GATE_OPERATOR_TOKEN, lambda: gate_operator_token(secret_env_var=secret_env_var)),
        (GATE_KILL_SWITCH, lambda: gate_kill_switch(ks_state=ks_state)),
        (GATE_PERSISTENCE_INTEGRITY, lambda: gate_persistence_integrity(conn=conn)),
        (GATE_MARKET_DATA, lambda: gate_market_data(market_data_provider=market_data_provider, instruments=instruments)),
    ]

    failed_at: int | None = None
    for i, (name, fn) in enumerate(gate_callables):
        if failed_at is not None:
            report.gates.append(
                GateOutcome(name=name, outcome="skipped", message="short-circuit")
            )
            continue
        outcome = fn()
        report.gates.append(outcome)
        structured_log(
            _LOGGER,
            logging.INFO if outcome.outcome == "ok" else logging.WARNING,
            "system",
            f"preflight:{outcome.name}",
            outcome=outcome.outcome,
            message=outcome.message,
        )
        if outcome.outcome == "failed":
            failed_at = i
            report.outcome = "failed"
    return report


# The `build_broker_for_preflight` factory lives in
# `trading_system/webapp/runtimes/preflight_broker.py` — under the
# documented `webapp/runtimes/` structural carve-out that's
# permitted to reach `execution.*` + `data.*`. The CLI imports it
# from there.


def write_report(report: PreflightReport, out_path: Path) -> None:
    """Write the report to ``out_path`` as canonical JSON. The
    serialiser is deterministic (sort_keys + tight separators) so
    two preflight runs with the same `(broker state, market-data,
    operator inputs, clock)` produce byte-identical files."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    out_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
