"""Pure converters between domain dataclasses and row dicts.

REQ refs:
- REQ_F_PER_005 — Decimal stored as TEXT; datetime as ISO-8601 with
  explicit timezone; no ``float`` past the persistence boundary.
- REQ_SDD_PER_003 — pure functions; no I/O; closed Err category set
  for parse failures.
- REQ_NF_PER_001 — write-then-read round-trips equal under
  structural comparison.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import OrderId, SnapshotId, StrategyId, TradeId
from trading_system.models.money import Currency, Money
from trading_system.models.safety import KillSwitchState
from trading_system.models.trading import Trade
from trading_system.safety.snapshot import AuditSnapshot
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.registry import RegistryEntry


# ---------------------------------------------------------------------------
# Equity points
# ---------------------------------------------------------------------------


def equity_point_to_row(p: EquityPoint, account_id: str) -> dict[str, str]:
    """Domain ``EquityPoint`` → row dict for ``equity_points``."""
    return {
        "account_id": account_id,
        "at": p.at.isoformat(),
        "equity_gross_amount": str(p.equity_gross.amount),
        "equity_gross_currency": p.equity_gross.currency.value,
        "equity_after_tax_amount": str(p.equity_after_tax.amount),
        "equity_after_tax_currency": p.equity_after_tax.currency.value,
        "drawdown_pct": str(p.drawdown_pct),
    }


def row_to_equity_point(row: dict[str, str]) -> EquityPoint:
    """Row dict → domain ``EquityPoint``. Decimal via
    ``Decimal(str(...))`` so float repr noise never leaks in."""
    return EquityPoint(
        at=datetime.fromisoformat(row["at"]),
        equity_gross=Money(
            Decimal(row["equity_gross_amount"]),
            Currency(row["equity_gross_currency"]),
        ),
        equity_after_tax=Money(
            Decimal(row["equity_after_tax_amount"]),
            Currency(row["equity_after_tax_currency"]),
        ),
        drawdown_pct=Decimal(row["drawdown_pct"]),
    )


# ---------------------------------------------------------------------------
# Money / Trade / EquityPoint JSON-shape helpers
#
# Used by both the BacktestResult mapper and the snapshot mapper. Decimal
# becomes a string; datetime becomes ISO-8601. The shape is verbose by
# design so round-tripping is unambiguous (REQ_NF_PER_001).
# ---------------------------------------------------------------------------


def _money_to_json(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency.value}


def _money_from_json(d: Mapping[str, str]) -> Money:
    return Money(Decimal(d["amount"]), Currency(d["currency"]))


def _equity_point_to_json(p: EquityPoint) -> dict[str, Any]:
    return {
        "at": p.at.isoformat(),
        "equity_gross": _money_to_json(p.equity_gross),
        "equity_after_tax": _money_to_json(p.equity_after_tax),
        "drawdown_pct": str(p.drawdown_pct),
    }


def _equity_point_from_json(d: Mapping[str, Any]) -> EquityPoint:
    return EquityPoint(
        at=datetime.fromisoformat(d["at"]),
        equity_gross=_money_from_json(d["equity_gross"]),
        equity_after_tax=_money_from_json(d["equity_after_tax"]),
        drawdown_pct=Decimal(d["drawdown_pct"]),
    )


def _trade_to_json(t: Trade) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "order_id": str(t.order_id),
        "executed_at": t.executed_at.isoformat(),
        "price": str(t.price),
        "quantity_filled": str(t.quantity_filled),
        "fees": _money_to_json(t.fees),
        "slippage": str(t.slippage),
    }


def _trade_from_json(d: Mapping[str, Any]) -> Trade:
    return Trade(
        id=TradeId(d["id"]),
        order_id=OrderId(d["order_id"]),
        executed_at=datetime.fromisoformat(d["executed_at"]),
        price=Decimal(d["price"]),
        quantity_filled=Decimal(d["quantity_filled"]),
        fees=_money_from_json(d["fees"]),
        slippage=Decimal(d["slippage"]),
    )


# ---------------------------------------------------------------------------
# StrategyMetrics + RegistryEntry
# ---------------------------------------------------------------------------


def _strategy_metrics_to_json(m: StrategyMetrics) -> dict[str, str]:
    return {
        "net_after_tax_return": str(m.net_after_tax_return),
        "sharpe": str(m.sharpe),
        "stability": str(m.stability),
        "dd_penalty": str(m.dd_penalty),
        "max_drawdown": str(m.max_drawdown),
        "turnover": str(m.turnover),
        "regime_stability": str(m.regime_stability),
        "leverage": str(m.leverage),
        "parameter_sensitivity": str(m.parameter_sensitivity),
        "risk": str(m.risk),
        "return_": str(m.return_),
    }


def _strategy_metrics_from_json(d: Mapping[str, str]) -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal(d["net_after_tax_return"]),
        sharpe=Decimal(d["sharpe"]),
        stability=Decimal(d["stability"]),
        dd_penalty=Decimal(d["dd_penalty"]),
        max_drawdown=Decimal(d["max_drawdown"]),
        turnover=Decimal(d["turnover"]),
        regime_stability=Decimal(d["regime_stability"]),
        leverage=Decimal(d["leverage"]),
        parameter_sensitivity=Decimal(d["parameter_sensitivity"]),
        risk=Decimal(d["risk"]),
        return_=Decimal(d["return_"]),
    )


def registry_entry_to_row(entry: RegistryEntry, account_id: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "strategy_id": str(entry.strategy_id),
        "git_sha": entry.git_sha,
        "config_hash": entry.config_hash,
        "seed": int(entry.seed),
        "metrics_json": json.dumps(
            _strategy_metrics_to_json(entry.metrics), separators=(",", ":")
        ),
        "validated": 1 if entry.validated else 0,
        "created_at": entry.created_at.isoformat(),
        "notes": entry.notes,
    }


def row_to_registry_entry(row: Mapping[str, Any]) -> RegistryEntry:
    return RegistryEntry(
        strategy_id=StrategyId(row["strategy_id"]),
        git_sha=row["git_sha"],
        config_hash=row["config_hash"],
        seed=int(row["seed"]),
        metrics=_strategy_metrics_from_json(json.loads(row["metrics_json"])),
        validated=bool(row["validated"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        notes=row["notes"],
    )


# ---------------------------------------------------------------------------
# BacktestResult — keyed JSON; round-trips bit-identically (TC_PER_007).
# ---------------------------------------------------------------------------


def backtest_result_to_json(result: BacktestResult) -> str:
    payload = {
        "trades": [_trade_to_json(t) for t in result.trades],
        "equity_curve": [_equity_point_to_json(p) for p in result.equity_curve],
        "equity_excl_injections": [str(x) for x in result.equity_excl_injections],
        "final_cash": _money_to_json(result.final_cash),
        "final_equity_after_tax": _money_to_json(result.final_equity_after_tax),
        "realized_gross": _money_to_json(result.realized_gross),
        "realized_after_tax": _money_to_json(result.realized_after_tax),
        "dividends_gross": _money_to_json(result.dividends_gross),
        "dividends_after_tax": _money_to_json(result.dividends_after_tax),
        "knockouts": result.knockouts,
        "injections_applied": result.injections_applied,
    }
    return json.dumps(payload, separators=(",", ":"))


def backtest_result_from_json(s: str) -> BacktestResult:
    d = json.loads(s)
    return BacktestResult(
        trades=tuple(_trade_from_json(t) for t in d["trades"]),
        equity_curve=tuple(_equity_point_from_json(p) for p in d["equity_curve"]),
        equity_excl_injections=tuple(Decimal(x) for x in d["equity_excl_injections"]),
        final_cash=_money_from_json(d["final_cash"]),
        final_equity_after_tax=_money_from_json(d["final_equity_after_tax"]),
        realized_gross=_money_from_json(d["realized_gross"]),
        realized_after_tax=_money_from_json(d["realized_after_tax"]),
        dividends_gross=_money_from_json(d["dividends_gross"]),
        dividends_after_tax=_money_from_json(d["dividends_after_tax"]),
        knockouts=int(d["knockouts"]),
        injections_applied=int(d["injections_applied"]),
    )


# ---------------------------------------------------------------------------
# AuditSnapshot — round-trips iff the original ``payload`` is already
# JSON-native (strings, ints, bools, lists, dicts thereof). Decimal /
# datetime values in ``payload`` survive the trip only as strings, which
# matches the existing ``FileSnapshotSink`` contract.
# ---------------------------------------------------------------------------


def audit_snapshot_to_row(s: AuditSnapshot, account_id: str) -> dict[str, str]:
    body = {
        "id": str(s.id),
        "at": s.at.isoformat(),
        "state_from": s.state_from.value,
        "state_to": s.state_to.value,
        "trigger_code": s.trigger_code,
        "trigger_message": s.trigger_message,
        "severity": s.severity,
        "payload": dict(s.payload),
    }
    return {
        "account_id": account_id,
        "snapshot_id": str(s.id),
        "captured_at": s.at.isoformat(),
        "snapshot_json": json.dumps(body, separators=(",", ":"), default=_json_default),
    }


def row_to_audit_snapshot(row: Mapping[str, Any]) -> AuditSnapshot:
    body = json.loads(row["snapshot_json"])
    return AuditSnapshot(
        id=SnapshotId(body["id"]),
        at=datetime.fromisoformat(body["at"]),
        state_from=KillSwitchState(body["state_from"]),
        state_to=KillSwitchState(body["state_to"]),
        trigger_code=body["trigger_code"],
        trigger_message=body["trigger_message"],
        severity=body["severity"],
        payload=body.get("payload", {}),
    )


def _json_default(obj: Any) -> Any:
    # Tolerate Decimal / datetime in ``payload`` so callers don't have to
    # pre-serialize. The fidelity contract for these values matches the
    # legacy ``FileSnapshotSink`` (str-cast for Decimal, isoformat for
    # datetime); see TC_PER_009.
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
