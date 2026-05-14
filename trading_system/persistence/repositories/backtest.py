"""``BacktestResultRepository`` — durable archive of every backtest
run keyed on the replay tuple (CR-008 / REQ_F_PER_007 / REQ_SDD_PER_006).

The archive key is ``(account_id, strategy_id, git_sha, config_hash,
seed)``. Replaying with the same key SHALL return a ``BacktestResult``
bit-identical to the one stored (REQ_NF_PER_001).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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


def _safe_rollback(conn: Connection) -> None:
    try:
        conn.rollback()
    except DatabaseError:
        pass
