"""``BacktestResultRepository`` — durable archive of every backtest
run keyed on the replay tuple (CR-008 / REQ_F_PER_007 / REQ_SDD_PER_006).

The archive key is ``(account_id, strategy_id, git_sha, config_hash,
seed)``. Replaying with the same key SHALL return a ``BacktestResult``
bit-identical to the one stored (REQ_NF_PER_001).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.backtesting.result import BacktestResult
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID, AccountId, StrategyId
from trading_system.persistence.connection import (
    Connection,
    DatabaseError,
    IntegrityError,
    OperationalError,
)
from trading_system.persistence.mappers import (
    backtest_result_from_json,
    backtest_result_to_json,
)
from trading_system.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class BacktestArchiveRow:
    """Lightweight row returned by ``list_archived``.

    Contains the archive key + the extracted metrics needed for
    the C10 list-backtests filter DSL. Full ``BacktestResult``
    deserialisation still happens through ``lookup`` — this row
    is the operator-facing summary surface.
    """

    account_id: AccountId
    strategy_id: StrategyId
    git_sha: str
    config_hash: str
    seed: int
    archived_at: datetime
    final_equity: Decimal
    final_equity_currency: str
    max_drawdown: Decimal
    realized_after_tax: Decimal
    trades_count: int
    knockouts: int


@dataclass(slots=True)
class BacktestResultRepository:
    """SQLite-backed backtest archive."""

    conn: Connection

    def archive(
        self,
        result: BacktestResult,
        *,
        strategy_id: StrategyId,
        git_sha: str,
        config_hash: str,
        seed: int,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[None, str]:
        """Persist ``result`` keyed on the replay tuple. A duplicate
        key replaces the prior archive — the engine should treat each
        re-run with the same key as authoritative (`REQ_NF_REP_001`
        guarantees the values are equal anyway)."""
        body = backtest_result_to_json(result)
        archived_at = datetime.now(tz=UTC).isoformat()
        try:
            self.conn.begin_immediate()
            self.conn.execute(
                "INSERT INTO backtest_results "
                "(account_id, strategy_id, git_sha, config_hash, seed, "
                " result_json, archived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, strategy_id, git_sha, config_hash, seed) "
                "DO UPDATE SET result_json = excluded.result_json, "
                "             archived_at = excluded.archived_at",
                (
                    str(account_id),
                    str(strategy_id),
                    git_sha,
                    config_hash,
                    int(seed),
                    body,
                    archived_at,
                ),
            )
            self.conn.commit()
        except IntegrityError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:integrity:backtest_results:{e}")
        except OperationalError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:locked:backtest_results:{e}")
        except DatabaseError as e:
            _safe_rollback(self.conn)
            return Err(f"persistence:corrupt:backtest_results:{e}")
        return Ok(None)

    def lookup(
        self,
        strategy_id: StrategyId,
        git_sha: str,
        config_hash: str,
        seed: int,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
    ) -> Result[BacktestResult, str]:
        """Return the archived ``BacktestResult`` for the exact tuple
        or ``Err("persistence:not_found:backtest_results:<key>")``."""
        try:
            cursor = self.conn.execute(
                "SELECT result_json FROM backtest_results "
                "WHERE account_id = ? AND strategy_id = ? AND git_sha = ? "
                "  AND config_hash = ? AND seed = ?",
                (str(account_id), str(strategy_id), git_sha, config_hash, int(seed)),
            )
            row = cursor.fetchone()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:backtest_results:read:{e}")
        if row is None:
            return Err(
                "persistence:not_found:backtest_results:"
                f"{account_id}/{strategy_id}/{git_sha}/{config_hash}/{seed}"
            )
        return Ok(backtest_result_from_json(row["result_json"]))

    def list_archived(
        self,
        *,
        account_id: AccountId = DEFAULT_ACCOUNT_ID,
        strategy_id: StrategyId | None = None,
        since: datetime | None = None,
    ) -> Result[tuple[BacktestArchiveRow, ...], str]:
        """C10 — operator-facing list of archived backtests.

        Returns lightweight ``BacktestArchiveRow`` rows ordered by
        ``archived_at DESC`` so the most-recent runs surface first.
        Optional filters:

        - ``strategy_id``: restrict to one strategy.
        - ``since``: only rows archived at-or-after this ISO-8601
          timestamp.

        The repo extracts metrics by deserialising
        ``result_json``; the operator-facing DSL filtering
        (e.g. ``--metric "sharpe>1.0"``) happens in the CLI
        layer so the persistence boundary stays narrow.
        """
        clauses = ["account_id = ?"]
        params: list[object] = [str(account_id)]
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(str(strategy_id))
        if since is not None:
            clauses.append("archived_at >= ?")
            params.append(since.isoformat())
        sql = (
            "SELECT strategy_id, git_sha, config_hash, seed, "
            "       result_json, archived_at "
            "FROM backtest_results "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY archived_at DESC, strategy_id ASC, "
            "         git_sha ASC, config_hash ASC, seed ASC"
        )
        try:
            cursor = self.conn.execute(sql, tuple(params))
            raw_rows = cursor.fetchall()
        except DatabaseError as e:
            return Err(f"persistence:corrupt:backtest_results:list:{e}")

        out: list[BacktestArchiveRow] = []
        for raw in raw_rows:
            try:
                payload = json.loads(raw["result_json"])
            except (ValueError, TypeError) as e:
                return Err(
                    f"persistence:corrupt:backtest_results:result_json:{e}"
                )
            metrics = _extract_archive_metrics(payload)
            out.append(
                BacktestArchiveRow(
                    account_id=account_id,
                    strategy_id=StrategyId(raw["strategy_id"]),
                    git_sha=raw["git_sha"],
                    config_hash=raw["config_hash"],
                    seed=int(raw["seed"]),
                    archived_at=datetime.fromisoformat(raw["archived_at"]),
                    final_equity=metrics["final_equity"],
                    final_equity_currency=metrics["final_equity_currency"],
                    max_drawdown=metrics["max_drawdown"],
                    realized_after_tax=metrics["realized_after_tax"],
                    trades_count=metrics["trades_count"],
                    knockouts=metrics["knockouts"],
                )
            )
        return Ok(tuple(out))


def _extract_archive_metrics(payload: dict) -> dict:
    """Pull the operator-facing metrics out of the persisted
    ``result_json`` payload. Defensive on missing fields — older
    archives without the documented shape surface ``Decimal("0")``
    placeholders so the list view stays renderable."""
    fe = payload.get("final_equity_after_tax", {}) or {}
    rat = payload.get("realized_after_tax", {}) or {}
    equity_curve = payload.get("equity_curve", []) or []
    max_dd = Decimal("0")
    for p in equity_curve:
        dd_raw = p.get("drawdown_pct") if isinstance(p, dict) else None
        if dd_raw is not None:
            try:
                dd = Decimal(str(dd_raw))
            except (ValueError, TypeError):
                continue
            if dd > max_dd:
                max_dd = dd
    return {
        "final_equity": Decimal(str(fe.get("amount", "0"))),
        "final_equity_currency": str(fe.get("currency", "")),
        "max_drawdown": max_dd,
        "realized_after_tax": Decimal(str(rat.get("amount", "0"))),
        "trades_count": int(len(payload.get("trades", []))),
        "knockouts": int(payload.get("knockouts", 0)),
    }


def _safe_rollback(conn: Connection) -> None:
    try:
        conn.rollback()
    except DatabaseError:
        pass
