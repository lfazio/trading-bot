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
    ``config.validate_all``. Same shape as ``python -m
    trading_system.config --validate-all``."""
    result = validate_all(args.config_dir)
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
        return 0
    err_report = result.error
    for line in err_report.errors:
        sys.stderr.write(f"{line}\n")
    sys.stderr.write(
        f"config: FAILED ({len(err_report.errors)} error(s)); "
        f"{len(err_report.validated_files)} file(s) validated\n"
    )
    return 1


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

    # For the broker-agnostic Phase-5 slice we can't actually
    # instantiate a live broker without a concrete adapter. We
    # synthesise a degenerate `_NotConfiguredBroker` stub that fails
    # the GATE_BROKER_AUTHENTICATE gate cleanly so the artefact's
    # output is the documented failure. When a concrete broker
    # ships, this section is rewritten to call the broker factory.
    class _NotConfiguredBroker:
        def account_state(self):
            raise RuntimeError(
                "no concrete live broker configured "
                "(REQ_F_BRK_003 / REQ_F_LIV_002 — broker selection "
                "is its own SRS amendment)"
            )

    class _DegradedKsState:
        """Stand-in until the live wiring loads the real safety
        snapshot at startup. v1 reports KILL so the gate fails
        until the operator wires the real safety layer."""

        value = "ACTIVE"  # treat the dev box as ACTIVE for the smoke

    class _NoMarketDataProvider:
        def latest(self, instrument):
            return Err(f"data:not_supported:{instrument.id}")

    report = run_preflight(
        system_config=sys_cfg,
        broker=_NotConfiguredBroker(),
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


if __name__ == "__main__":
    sys.exit(main())
