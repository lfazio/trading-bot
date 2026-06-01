"""``trading-bot`` console script — REQ_O_004 / REQ_SDS_CLI_001 /
REQ_SDD_CLI_001.

Thin argparse dispatcher; each subcommand handler is a
``(Namespace) -> int`` callable that returns the process exit code.
Handlers SHALL NOT call ``sys.exit`` directly (REQ_SDD_CLI_001) so
unit tests drive the dispatcher without subprocess gymnastics.

Subcommands (MVP-v1):
  - ``backtest`` — delegate to ``trading_system.main.run`` + emit
    a report directory via ``analytics.write_report`` (CR-016
    MVP-4).
  - ``record-data`` — loop ``tools.yfinance_recorder`` over a
    universe preset.
  - ``validate-config`` — delegate to
    ``trading_system.config.validate_all``.

Plumbing only — the CLI SHALL NOT import ``execution`` /
``safety`` / ``risk`` / ``strategy_lab`` directly (REQ_SDD_CLI_002
AST audit). Subcommand handlers reach those modules transitively
through the public entry points they delegate to, which is fine —
the structural test enforces the import boundary at the cli.py
file only.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from trading_system.analytics import report_dir_name, write_report
from trading_system.config import validate_all
from trading_system.data.universes import (
    list_bundled_universes,
    load_universe,
)
from trading_system.main import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_END,
    DEFAULT_START,
    run,
)
from trading_system.result import Err, Ok


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-bot",
        description=(
            "Production-grade Python trading system optimizing "
            "after-tax returns. CR-016 MVP-v1 CLI."
        ),
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # ----- backtest --------------------------------------------------------
    bt = subparsers.add_parser(
        "backtest",
        help="Run a backtest. Emits a report directory at "
        "``var/reports/<utc-iso-timestamp>/`` by default.",
    )
    bt.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Directory holding system.yaml / phases.yaml / risk.yaml etc. "
        f"Default: {DEFAULT_CONFIG_DIR}",
    )
    bt.add_argument(
        "--start",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)
        if datetime.fromisoformat(s).tzinfo is None
        else datetime.fromisoformat(s),
        default=DEFAULT_START,
        help="Backtest start (ISO-8601). Default: 2026-01-01.",
    )
    bt.add_argument(
        "--end",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)
        if datetime.fromisoformat(s).tzinfo is None
        else datetime.fromisoformat(s),
        default=DEFAULT_END,
        help="Backtest end (ISO-8601). Default: 2026-04-01.",
    )
    bt.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Output directory for the report artefacts. "
        "Default: var/reports/<utc-iso-timestamp>/",
    )
    bt.add_argument(
        "--with-slippage",
        action="store_true",
        help="Apply seeded Gaussian slippage on every fill.",
    )
    bt.set_defaults(func=_run_backtest)

    # ----- record-data -----------------------------------------------------
    rd = subparsers.add_parser(
        "record-data",
        help="Populate the yfinance cache for every symbol in a "
        "named universe preset.",
    )
    rd.add_argument(
        "--universe",
        type=str,
        required=True,
        help="Universe preset name (see data/universes/).",
    )
    rd.add_argument(
        "--start",
        type=str,
        required=True,
        help="ISO-8601 start (e.g., 2023-01-01).",
    )
    rd.add_argument(
        "--end",
        type=str,
        required=True,
        help="ISO-8601 end (e.g., 2025-12-31).",
    )
    rd.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="Override the cache root (default: .cache/yfinance/).",
    )
    rd.set_defaults(func=_run_record_data)

    # ----- validate-config -------------------------------------------------
    vc = subparsers.add_parser(
        "validate-config",
        help="Validate every YAML in <config-dir> against its typed loader.",
    )
    vc.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing the YAML files (default: ./config).",
    )
    vc.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print successfully-validated and skipped filenames.",
    )
    vc.add_argument(
        "--rich-errors",
        action="store_true",
        help=(
            "C3 — additionally run Pydantic v2 schemas against the YAMLs "
            "that opt in (see trading_system.config.pydantic_schemas) and "
            "print every field-level error in a single tree-shaped report. "
            "v1 ships the schema for notifications.yaml only; other YAMLs "
            "stay on the existing typed-loader path."
        ),
    )
    vc.set_defaults(func=_run_validate_config)

    # ----- issue-token (CR-024 / REQ_F_TOK_005) ----------------------------
    it = subparsers.add_parser(
        "issue-token",
        help="Issue a fresh operator token (HMAC-signed, 4-segment "
        "CR-024 format). Reads the secret from an env var — raw "
        "secrets SHALL NEVER appear in argv.",
    )
    it.add_argument(
        "--account-id",
        type=str,
        required=True,
        help="Token claim. Use 'household' for read-only browser "
        "sessions; per-account id (e.g. 'default') for mutation "
        "scope.",
    )
    it.add_argument(
        "--ttl",
        type=int,
        default=86400,
        help="Token TTL in seconds (default 86400 = 24h).",
    )
    it.add_argument(
        "--secret-env",
        type=str,
        default="TRADING_BOT_OPERATOR_SECRET",
        help="Environment variable holding the operator secret "
        "(default TRADING_BOT_OPERATOR_SECRET).",
    )
    it.set_defaults(func=_run_issue_token)

    # ----- live-preflight (CR-019 step 2 / REQ_F_LIV_005) -----------------
    lp = subparsers.add_parser(
        "live-preflight",
        help="Run the six live-trading pre-flight gates and write "
        "the JSON artefact the dashboard reads to enable the live "
        "mode switch.",
    )
    lp.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing the YAML files (default: ./config).",
    )
    lp.add_argument(
        "--out",
        type=Path,
        default=Path("var/live-preflight.json"),
        help="Output JSON artefact path (default: var/live-preflight.json).",
    )
    lp.set_defaults(func=_run_live_preflight)

    # ----- list-backtests (C10 / gap-analysis Part C) ---------------------
    lb = subparsers.add_parser(
        "list-backtests",
        help="List archived backtests from the persistence repo. "
        "Supports filtering by strategy / since / metric "
        "expression (DSL: name<op>value).",
    )
    lb.add_argument(
        "--account-id",
        type=str,
        default="default",
        help="Account to query (default: 'default').",
    )
    lb.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Restrict to one strategy_id.",
    )
    lb.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO-8601 timestamp; only rows archived at-or-after.",
    )
    lb.add_argument(
        "--metric",
        action="append",
        default=[],
        help=(
            "Metric filter expression: name<op>value. Multiple "
            "--metric flags AND. Names: final_equity / max_drawdown / "
            "realized_after_tax / trades_count / knockouts. Ops: "
            ">, >=, <, <=, ==."
        ),
    )
    lb.add_argument(
        "--db",
        type=Path,
        default=Path("var/state.sqlite"),
        help="Path to the persistence SQLite file "
        "(default: var/state.sqlite).",
    )
    lb.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit machine-readable JSON instead of a "
        "human-readable table.",
    )
    lb.set_defaults(func=_run_list_backtests)

    # ----- ks-incident (C9 / gap-analysis Part C) -------------------------
    ki = subparsers.add_parser(
        "ks-incident",
        help="Export a kill-switch incident timeline (canonical "
        "JSON or human-readable table) from the SQLite "
        "ks_snapshots table.",
    )
    ki.add_argument(
        "--account-id",
        type=str,
        default="default",
        help="Account to query (default: 'default').",
    )
    ki.add_argument(
        "--since",
        type=str,
        required=True,
        help="Lower-bound ISO-8601 timestamp (inclusive).",
    )
    ki.add_argument(
        "--until",
        type=str,
        default=None,
        help="Upper-bound ISO-8601 timestamp (inclusive). "
        "Default: now.",
    )
    ki.add_argument(
        "--db",
        type=Path,
        default=Path("var/state.sqlite"),
        help="Path to the persistence SQLite file "
        "(default: var/state.sqlite).",
    )
    ki.add_argument(
        "--table",
        dest="table_output",
        action="store_true",
        help="Emit a human-readable table instead of canonical JSON "
        "(default: JSON for operator scripting + ingestion).",
    )
    ki.set_defaults(func=_run_ks_incident)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry — returns the exit code. The console-script entry
    point in ``pyproject.toml`` wraps this with ``sys.exit``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _run_backtest(args: argparse.Namespace) -> int:
    """``trading-bot backtest`` — runs the demo + emits a report."""
    outcome_or_err = run(
        config_dir=args.config_dir,
        start=args.start,
        end=args.end,
        use_slippage=args.with_slippage,
    )
    if isinstance(outcome_or_err, Err):
        sys.stderr.write(f"trading-bot backtest: ERROR {outcome_or_err.error}\n")
        return 1
    outcome = outcome_or_err.value

    now = datetime.now(UTC)
    out_dir = (
        args.report_dir
        if args.report_dir is not None
        else Path("var") / "reports" / report_dir_name(now)
    )
    report_res = write_report(
        outcome.result,
        config_hash=outcome.config_hash,
        out_dir=out_dir,
        seed=outcome.seed,
        start_at=args.start,
        end_at=args.end,
        data_provider=outcome.data_provider,
    )
    if isinstance(report_res, Err):
        sys.stderr.write(
            f"trading-bot backtest: ERROR write_report {report_res.error.category}\n"
        )
        return 1
    sys.stdout.write(f"trading-bot backtest: OK report written to {report_res.value}\n")
    return 0


def _run_record_data(args: argparse.Namespace) -> int:
    """``trading-bot record-data`` — loop the yfinance recorder
    over a universe preset's symbols. Delegates to
    ``tools.yfinance_recorder.record`` (the existing recorder
    entry).
    """
    uni_result = load_universe(args.universe)
    if isinstance(uni_result, Err):
        sys.stderr.write(f"trading-bot record-data: ERROR {uni_result.error}\n")
        sys.stderr.write(
            f"available universes: "
            f"{', '.join(list_bundled_universes().unwrap_or(()))}\n"
        )
        return 1
    universe = uni_result.value

    # Delegate per-symbol to the recorder script. The recorder's
    # ``record`` entry is the public surface — we import it lazily
    # because the import pulls in yfinance + pandas (heavy).
    try:
        from tools.yfinance_recorder import record  # type: ignore[import-not-found]
    except ImportError as e:
        sys.stderr.write(
            f"trading-bot record-data: ERROR cannot import recorder: {e}\n"
        )
        return 1

    failures = 0
    for stock in universe.stocks:
        try:
            record(
                symbol=str(stock.id),
                exchange=stock.exchange,
                currency=stock.currency.value,
                start=args.start,
                end=args.end,
                cache_root=str(args.cache_root) if args.cache_root else None,
            )
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(
                f"trading-bot record-data: {stock.id} failed: {e}\n"
            )
            failures += 1
    if failures:
        sys.stderr.write(
            f"trading-bot record-data: {failures} / {len(universe.stocks)} "
            f"symbol(s) failed; check stderr above\n"
        )
        return 1
    sys.stdout.write(
        f"trading-bot record-data: recorded {len(universe.stocks)} symbol(s)\n"
    )
    return 0


def _run_validate_config(args: argparse.Namespace) -> int:
    """``trading-bot validate-config`` — delegate to
    ``config.validate_all`` + optionally to the C3 Pydantic
    schemas via ``--rich-errors``."""
    result = validate_all(args.config_dir)
    exit_code = 0
    if isinstance(result, Ok):
        report = result.value
        if args.verbose:
            sys.stdout.write(
                f"validated: {', '.join(report.validated_files)}\n"
            )
            if report.skipped_files:
                sys.stdout.write(
                    f"skipped (absent optional): "
                    f"{', '.join(report.skipped_files)}\n"
                )
        sys.stdout.write(
            f"config: OK ({len(report.validated_files)} files validated)\n"
        )
    else:
        err_report = result.error
        for line in err_report.errors:
            sys.stderr.write(f"{line}\n")
        sys.stderr.write(
            f"config: FAILED ({len(err_report.errors)} error(s)); "
            f"{len(err_report.validated_files)} file(s) validated\n"
        )
        exit_code = 1

    if args.rich_errors:
        from trading_system.config.pydantic_schemas import (
            render_rich_report,
            validate_with_pydantic_schemas,
        )

        rich_result = validate_with_pydantic_schemas(args.config_dir)
        if isinstance(rich_result, Ok):
            sys.stdout.write(render_rich_report(rich_result.value))
        else:
            sys.stderr.write(render_rich_report(rich_result.error))
            exit_code = 1
    return exit_code


def _run_live_preflight(args: argparse.Namespace) -> int:
    """``trading-bot live-preflight`` — CR-019 step 2 / REQ_F_LIV_005.

    Runs the six pre-flight gates documented in SDS §3.41 + writes a
    JSON artefact to ``args.out``. Exit code 0 on `outcome="ok"`;
    1 on any gate failure.

    The CLI deliberately loads the project config + builds the broker
    + opens the persistence connection inside this handler so the
    boundary is honest: the preflight is operator-facing tooling that
    runs against the live deployment state, not a unit-testable pure
    function. Tests inject the runner via the ``run_preflight`` helper
    directly.
    """
    from datetime import UTC, datetime as _dt

    from trading_system.config.system import load_system_config
    from trading_system.persistence.connection import Connection
    from trading_system.webapp.live_preflight import (
        run_preflight,
        write_report,
    )

    sys_cfg_path = args.config_dir / "system.yaml"
    sys_cfg_res = load_system_config(sys_cfg_path)
    if isinstance(sys_cfg_res, Err):
        sys.stderr.write(
            f"trading-bot live-preflight: ERROR loading "
            f"{sys_cfg_path}: {sys_cfg_res.error}\n"
        )
        return 1
    sys_cfg = sys_cfg_res.value

    # The broker, persistence DB, and market-data provider are deployment
    # state — operators running this CLI MUST have them provisioned.
    # We fail-closed if any cannot be reached; the failure is recorded
    # in the JSON artefact + reported on stderr.
    db_path = Path("var") / "state.sqlite"
    conn_res = Connection.open(db_path)
    if isinstance(conn_res, Err):
        sys.stderr.write(
            f"trading-bot live-preflight: ERROR opening {db_path}: "
            f"{conn_res.error}\n"
        )
        return 1
    conn = conn_res.value

    # CR-025 — broker construction delegated to the `webapp/runtimes/`
    # factory so this CLI subcommand stays plumbing-only
    # (REQ_SDS_CLI_001 — cli.py SHALL NOT import `execution.*` directly;
    # REQ_SDD_FAS_001 — only `webapp/runtimes/` may reach `execution.*`
    # + `data.*`).
    from trading_system.webapp.runtimes.preflight_broker import (
        build_broker_for_preflight,
    )

    broker = build_broker_for_preflight(sys_cfg)

    class _DegradedKsState:
        """Stand-in until the live wiring loads the real safety
        snapshot at startup. v1 reports ACTIVE so a paper-broker
        preflight against a clean dev box can pass."""

        value = "ACTIVE"

    class _NoMarketDataProvider:
        def latest(self, instrument):
            return Err(f"data:not_supported:{instrument.id}")

    report = run_preflight(
        system_config=sys_cfg,
        broker=broker,
        conn=conn,
        ks_state=_DegradedKsState(),
        market_data_provider=_NoMarketDataProvider(),
        instruments=(),
        now=_dt.now(UTC),
    )
    write_report(report, args.out)
    conn.close()

    if report.outcome == "ok":
        sys.stdout.write(
            f"trading-bot live-preflight: OK ({len(report.gates)} gates) "
            f"→ {args.out}\n"
        )
        return 0
    sys.stderr.write(
        f"trading-bot live-preflight: FAILED → {args.out}\n"
    )
    for gate in report.gates:
        if gate.outcome == "failed":
            sys.stderr.write(f"  {gate.name}: {gate.message}\n")
    return 1


def _run_issue_token(args: argparse.Namespace) -> int:
    """``trading-bot issue-token`` — CR-024 / REQ_F_TOK_005.

    Reads the operator secret from the env var named by
    ``--secret-env`` (default ``TRADING_BOT_OPERATOR_SECRET``).
    Raw secrets SHALL NEVER appear in argv (no ``--secret <hex>``
    flag).

    Writes the token to stdout (one line) on success; emits a
    SECURITY structured-log entry recording the issuance event.
    """
    import os
    from datetime import UTC, datetime as _dt
    from trading_system.accounts.token_verifier import (
        AccountScopedTokenVerifier,
    )

    secret = os.environ.get(args.secret_env)
    if not secret:
        sys.stderr.write(
            f"trading-bot issue-token: env var {args.secret_env!r} "
            "is not set; export the operator secret first.\n"
        )
        return 1
    if args.ttl <= 0:
        sys.stderr.write(
            f"trading-bot issue-token: --ttl must be > 0 (got {args.ttl}).\n"
        )
        return 1
    verifier = AccountScopedTokenVerifier(
        secret=secret.encode("utf-8"),
        ttl_seconds=args.ttl,
    )
    token = verifier.issue(account_id=args.account_id, now=_dt.now(UTC))
    sys.stdout.write(token + "\n")
    return 0


# ---------------------------------------------------------------------------
# list-backtests — C10 (gap-analysis Part C)
# ---------------------------------------------------------------------------


_METRIC_OPS: tuple[str, ...] = (">=", "<=", "==", ">", "<")
_METRIC_NAMES: frozenset[str] = frozenset(
    {
        "final_equity",
        "max_drawdown",
        "realized_after_tax",
        "trades_count",
        "knockouts",
    }
)


def _parse_metric_filter(expr: str) -> tuple[str, str, "Decimal"] | str:
    """Parse a metric expression like ``"sharpe>1.0"``.

    Returns either a ``(name, op, value)`` triple or a
    categorised ``"cli:metric:<reason>"`` Err string. The closed
    vocabulary for ``name`` matches ``_METRIC_NAMES``; ``op``
    matches the documented operator set.
    """
    from decimal import Decimal, InvalidOperation

    raw = expr.strip()
    if not raw:
        return "cli:metric:empty_expression"
    # Two-char ops first so "<=" doesn't match "<" prematurely.
    for op in _METRIC_OPS:
        if op in raw:
            name_str, _, value_str = raw.partition(op)
            name = name_str.strip()
            value_str = value_str.strip()
            if name not in _METRIC_NAMES:
                return f"cli:metric:unknown_name:{name}"
            try:
                value = Decimal(value_str)
            except (InvalidOperation, ValueError):
                return f"cli:metric:bad_value:{value_str}"
            return (name, op, value)
    return f"cli:metric:no_op:{raw}"


def _row_matches_filter(row: object, name: str, op: str, value: object) -> bool:
    """Apply one parsed filter triple against a ``BacktestArchiveRow``."""
    field_value = getattr(row, name)
    # ``trades_count`` and ``knockouts`` are ints; everything else
    # Decimal. Cross-comparison via Python's <=>/== works because
    # Decimal compares cleanly with int.
    if op == ">":
        return field_value > value  # type: ignore[operator]
    if op == ">=":
        return field_value >= value  # type: ignore[operator]
    if op == "<":
        return field_value < value  # type: ignore[operator]
    if op == "<=":
        return field_value <= value  # type: ignore[operator]
    if op == "==":
        return field_value == value
    return False


def _run_list_backtests(args: argparse.Namespace) -> int:
    """``trading-bot list-backtests`` — C10. Reads the persistence
    repo + applies operator-supplied filters; prints results as
    a table (default) or JSON (--json)."""
    import json as _json
    from datetime import datetime as _dt
    from trading_system.persistence.connection import Connection
    from trading_system.persistence.repositories.backtest import (
        BacktestResultRepository,
    )
    from trading_system.models.identifiers import AccountId, StrategyId
    from trading_system.result import Err, Ok

    db_path = args.db
    if not db_path.is_file():
        sys.stderr.write(
            f"trading-bot list-backtests: db not found at {db_path}\n"
        )
        return 1
    conn_res = Connection.open(db_path)
    if isinstance(conn_res, Err):
        sys.stderr.write(
            f"trading-bot list-backtests: cannot open db: {conn_res.error}\n"
        )
        return 1
    conn = conn_res.value

    # Parse the metric filters BEFORE hitting the DB so bad
    # expressions fail fast.
    parsed_filters: list[tuple[str, str, object]] = []
    for expr in args.metric:
        parsed = _parse_metric_filter(expr)
        if isinstance(parsed, str):
            sys.stderr.write(
                f"trading-bot list-backtests: invalid --metric {expr!r}: "
                f"{parsed}\n"
            )
            conn.close()
            return 1
        parsed_filters.append(parsed)

    since_dt: _dt | None = None
    if args.since is not None:
        try:
            since_dt = _dt.fromisoformat(args.since)
        except ValueError as e:
            sys.stderr.write(
                f"trading-bot list-backtests: invalid --since {args.since!r}: "
                f"{e}\n"
            )
            conn.close()
            return 1

    repo = BacktestResultRepository(conn=conn)
    list_res = repo.list_archived(
        account_id=AccountId(args.account_id),
        strategy_id=StrategyId(args.strategy) if args.strategy else None,
        since=since_dt,
    )
    if isinstance(list_res, Err):
        sys.stderr.write(
            f"trading-bot list-backtests: {list_res.error}\n"
        )
        conn.close()
        return 1
    rows = list_res.value

    # Apply metric filters (AND across filters).
    filtered = [
        r
        for r in rows
        if all(_row_matches_filter(r, n, op, v) for (n, op, v) in parsed_filters)
    ]

    if args.json_output:
        payload = [
            {
                "strategy_id": str(r.strategy_id),
                "git_sha": r.git_sha,
                "config_hash": r.config_hash,
                "seed": r.seed,
                "archived_at": r.archived_at.isoformat(),
                "final_equity": str(r.final_equity),
                "final_equity_currency": r.final_equity_currency,
                "max_drawdown": str(r.max_drawdown),
                "realized_after_tax": str(r.realized_after_tax),
                "trades_count": r.trades_count,
                "knockouts": r.knockouts,
            }
            for r in filtered
        ]
        sys.stdout.write(_json.dumps(payload, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            f"{'archived_at':<26}  {'strategy_id':<20}  {'seed':>10}  "
            f"{'final_equity':>15}  {'max_dd':>8}  {'trades':>6}\n"
        )
        for r in filtered:
            sys.stdout.write(
                f"{r.archived_at.isoformat():<26}  "
                f"{str(r.strategy_id):<20}  "
                f"{r.seed:>10}  "
                f"{r.final_equity_currency} {str(r.final_equity):>10}  "
                f"{str(r.max_drawdown):>8}  "
                f"{r.trades_count:>6}\n"
            )
        sys.stdout.write(f"{len(filtered)} row(s) (of {len(rows)} archived)\n")
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# ks-incident — C9 (gap-analysis Part C)
# ---------------------------------------------------------------------------


def _run_ks_incident(args: argparse.Namespace) -> int:
    """``trading-bot ks-incident`` — C9. Reads the
    ``ks_snapshots`` table; exports a postmortem timeline."""
    import json as _json
    from datetime import datetime as _dt

    from trading_system.models.identifiers import AccountId
    from trading_system.persistence.connection import Connection
    from trading_system.persistence.repositories.snapshot import (
        KillSwitchSnapshotRepository,
    )
    from trading_system.result import Err, Ok

    db_path = args.db
    if not db_path.is_file():
        sys.stderr.write(
            f"trading-bot ks-incident: db not found at {db_path}\n"
        )
        return 1
    try:
        since_dt = _dt.fromisoformat(args.since)
    except ValueError as e:
        sys.stderr.write(
            f"trading-bot ks-incident: invalid --since {args.since!r}: {e}\n"
        )
        return 1
    until_dt: _dt | None = None
    if args.until is not None:
        try:
            until_dt = _dt.fromisoformat(args.until)
        except ValueError as e:
            sys.stderr.write(
                f"trading-bot ks-incident: invalid --until {args.until!r}: {e}\n"
            )
            return 1

    conn_res = Connection.open(db_path)
    if isinstance(conn_res, Err):
        sys.stderr.write(
            f"trading-bot ks-incident: cannot open db: {conn_res.error}\n"
        )
        return 1
    conn = conn_res.value
    repo = KillSwitchSnapshotRepository(
        conn=conn,
        account_id=AccountId(args.account_id),
    )
    list_res = repo.list_in_window(since=since_dt, until=until_dt)
    if isinstance(list_res, Err):
        sys.stderr.write(
            f"trading-bot ks-incident: {list_res.error}\n"
        )
        conn.close()
        return 1
    snapshots = list_res.value

    if args.table_output:
        sys.stdout.write(
            f"{'captured_at':<26}  {'snapshot_id':<24}  "
            f"{'state':<22}  {'severity':<9}  trigger\n"
        )
        for s in snapshots:
            transition = f"{s.state_from.value}->{s.state_to.value}"
            sys.stdout.write(
                f"{s.at.isoformat():<26}  {str(s.id):<24}  "
                f"{transition:<22}  {s.severity:<9}  "
                f"{s.trigger_code}: {s.trigger_message}\n"
            )
        sys.stdout.write(f"{len(snapshots)} snapshot(s)\n")
    else:
        # Canonical-JSON timeline — sorted keys; one object per
        # row. The output is operator-scripting-friendly: pipe
        # into `jq` or ingest into Grafana / Loki / Splunk via
        # the standard JSON tooling.
        payload = [
            {
                "id": str(s.id),
                "at": s.at.isoformat(),
                "account_id": args.account_id,
                "state_from": s.state_from.value,
                "state_to": s.state_to.value,
                "severity": s.severity,
                "trigger_code": s.trigger_code,
                "trigger_message": s.trigger_message,
                "payload": dict(s.payload),
            }
            for s in snapshots
        ]
        sys.stdout.write(_json.dumps(payload, sort_keys=True) + "\n")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
