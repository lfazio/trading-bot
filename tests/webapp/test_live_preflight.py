"""CR-019 step 2 / TC_LIV_005..007 — operator pre-flight.

Tests the runner directly (gate composition + short-circuit + JSON
artefact shape). The CLI subcommand smoke lives in
``tests/test_cli.py``.

REQ refs: REQ_F_LIV_002, REQ_F_LIV_005, REQ_SDD_LIV_004, REQ_NF_LIV_001.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.persistence.connection import Connection
from trading_system.result import Err, Ok
from trading_system.webapp.live_preflight import (
    GATES_IN_ORDER,
    PreflightReport,
    run_preflight,
    write_report,
)


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


@dataclass
class _FakeSystemConfig:
    broker_adapter: str = "xtb"


@dataclass
class _OkBroker:
    def account_state(self):
        from dataclasses import dataclass as _dc

        @_dc
        class _State:
            cash: int = 100
            equity: int = 100

        return _State()


@dataclass
class _FailingBroker:
    def account_state(self):
        raise RuntimeError("not authenticated")


@dataclass
class _ActiveKs:
    value: str = "ACTIVE"


@dataclass
class _DegradedKs:
    value: str = "DEGRADED"


@dataclass
class _OkMarketData:
    def latest(self, instrument):
        return Ok(None)


@dataclass
class _FailingMarketData:
    def latest(self, instrument):
        return Err(f"data:not_found:{instrument.id}")


def _instrument(symbol: str = "ASML.AS") -> Stock:
    return Stock(
        id=InstrumentId(symbol),
        symbol=symbol.split(".")[0],
        exchange="AS",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin="NL0010273215",
        sector="tech",
        country="NL",
    )


@pytest.fixture
def healthy_conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "state.sqlite"
    connection = Connection.open(db_path).unwrap()
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# TC_LIV_005 — happy path
# ---------------------------------------------------------------------------


def test_happy_path_all_six_gates_ok(
    healthy_conn: Connection, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
    report = run_preflight(
        system_config=_FakeSystemConfig(broker_adapter="xtb"),
        broker=_OkBroker(),
        conn=healthy_conn,
        ks_state=_ActiveKs(),
        market_data_provider=_OkMarketData(),
        instruments=(_instrument(),),
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    assert report.outcome == "ok"
    assert len(report.gates) == 6
    assert [g.name for g in report.gates] == list(GATES_IN_ORDER)
    for gate in report.gates:
        assert gate.outcome == "ok"


def test_write_report_emits_canonical_json(
    healthy_conn: Connection, monkeypatch, tmp_path: Path
) -> None:
    """REQ_SDD_LIV_004 — sort_keys + tight separators so two runs
    against the same state produce byte-identical artefacts."""
    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
    report = run_preflight(
        system_config=_FakeSystemConfig(broker_adapter="xtb"),
        broker=_OkBroker(),
        conn=healthy_conn,
        ks_state=_ActiveKs(),
        market_data_provider=_OkMarketData(),
        instruments=(),
        now=datetime(2026, 5, 26, 12, tzinfo=UTC),
    )
    out = tmp_path / "live-preflight.json"
    write_report(report, out)
    text = out.read_text(encoding="utf-8")
    # Tight separators — no spaces after commas / colons.
    assert ", " not in text
    assert ": " not in text
    # JSON parses cleanly.
    parsed = json.loads(text)
    assert parsed["outcome"] == "ok"
    assert parsed["checked_at"] == "2026-05-26T12:00:00+00:00"
    assert len(parsed["gates"]) == 6


# ---------------------------------------------------------------------------
# TC_LIV_006 — short-circuit on first failure
# ---------------------------------------------------------------------------


class TestShortCircuit:
    def test_broker_selector_local_fails_first_gate(
        self, healthy_conn: Connection, monkeypatch
    ) -> None:
        monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
        report = run_preflight(
            system_config=_FakeSystemConfig(broker_adapter="local"),
            broker=_OkBroker(),
            conn=healthy_conn,
            ks_state=_ActiveKs(),
            market_data_provider=_OkMarketData(),
            instruments=(),
            now=datetime(2026, 5, 26, 12, tzinfo=UTC),
        )
        assert report.outcome == "failed"
        # First gate failed; remaining five skipped.
        assert report.gates[0].outcome == "failed"
        assert report.gates[0].name == "broker_selector"
        for gate in report.gates[1:]:
            assert gate.outcome == "skipped"

    def test_broker_authenticate_failure_short_circuits(
        self, healthy_conn: Connection, monkeypatch
    ) -> None:
        monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
        report = run_preflight(
            system_config=_FakeSystemConfig(broker_adapter="xtb"),
            broker=_FailingBroker(),
            conn=healthy_conn,
            ks_state=_ActiveKs(),
            market_data_provider=_OkMarketData(),
            instruments=(),
        )
        assert report.outcome == "failed"
        assert report.gates[0].outcome == "ok"
        assert report.gates[1].outcome == "failed"
        assert report.gates[1].name == "broker_authenticate"
        for gate in report.gates[2:]:
            assert gate.outcome == "skipped"

    def test_missing_operator_token_short_circuits(
        self, healthy_conn: Connection, monkeypatch
    ) -> None:
        monkeypatch.delenv("TRADING_BOT_OPERATOR_SECRET", raising=False)
        report = run_preflight(
            system_config=_FakeSystemConfig(broker_adapter="xtb"),
            broker=_OkBroker(),
            conn=healthy_conn,
            ks_state=_ActiveKs(),
            market_data_provider=_OkMarketData(),
            instruments=(),
        )
        assert report.outcome == "failed"
        assert report.gates[2].name == "operator_token"
        assert report.gates[2].outcome == "failed"

    def test_kill_switch_degraded_short_circuits(
        self, healthy_conn: Connection, monkeypatch
    ) -> None:
        monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
        report = run_preflight(
            system_config=_FakeSystemConfig(broker_adapter="xtb"),
            broker=_OkBroker(),
            conn=healthy_conn,
            ks_state=_DegradedKs(),
            market_data_provider=_OkMarketData(),
            instruments=(),
        )
        assert report.outcome == "failed"
        assert report.gates[3].name == "kill_switch"
        assert report.gates[3].outcome == "failed"

    def test_market_data_failure_short_circuits(
        self, healthy_conn: Connection, monkeypatch
    ) -> None:
        monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke" * 8)
        report = run_preflight(
            system_config=_FakeSystemConfig(broker_adapter="xtb"),
            broker=_OkBroker(),
            conn=healthy_conn,
            ks_state=_ActiveKs(),
            market_data_provider=_FailingMarketData(),
            instruments=(_instrument(),),
        )
        assert report.outcome == "failed"
        assert report.gates[5].name == "market_data"
        assert report.gates[5].outcome == "failed"


# ---------------------------------------------------------------------------
# PreflightReport invariants
# ---------------------------------------------------------------------------


def test_report_to_dict_shape() -> None:
    from trading_system.webapp.live_preflight import GateOutcome

    report = PreflightReport(
        checked_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
        outcome="ok",
        gates=[GateOutcome(name="broker_selector", outcome="ok", message="x")],
    )
    payload = report.to_dict()
    assert payload["checked_at"] == "2026-05-26T12:00:00+00:00"
    assert payload["outcome"] == "ok"
    assert len(payload["gates"]) == 1
    assert payload["gates"][0]["name"] == "broker_selector"


def test_gate_outcome_rejects_unknown_outcome_value() -> None:
    from trading_system.webapp.live_preflight import GateOutcome

    with pytest.raises(ValueError, match="GateOutcome.outcome"):
        GateOutcome(name="x", outcome="weird", message="")
