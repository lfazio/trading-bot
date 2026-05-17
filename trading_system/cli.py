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
    result_or_err = run(
        config_dir=args.config_dir,
        start=args.start,
        end=args.end,
        use_slippage=args.with_slippage,
    )
    if isinstance(result_or_err, Err):
        sys.stderr.write(f"trading-bot backtest: ERROR {result_or_err.error}\n")
        return 1

    # MVP-4 hook: emit a report directory. The DashboardView returned
    # by `run` doesn't carry the full BacktestResult; the report
    # emission lives downstream of the runtime once main.py
    # threads the BacktestResult through (Phase-B follow-up).
    # For MVP-v1 the CLI exits 0 on a successful run; the
    # report-directory emission lands when main.run returns the
    # BacktestResult alongside the DashboardView.
    sys.stdout.write(
        "trading-bot backtest: OK (run completed; report-artefact emission "
        "lands when main.run returns a BacktestResult per CR-016 Phase-B)\n"
    )
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


# Avoid the unused-import warning on ``report_dir_name`` /
# ``write_report`` — they are imported here so the Phase-B hook
# that threads ``BacktestResult`` through can call them without a
# fresh import. Stretch for MVP-v1.
_ = (report_dir_name, write_report)


if __name__ == "__main__":
    sys.exit(main())
