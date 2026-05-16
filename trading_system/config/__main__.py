"""CLI entry: ``python -m trading_system.config --validate-all <dir>``.

Exits non-zero on validation failure; prints every categorised
``Err`` so the operator fixes them in one cycle. The runner is
read-only — it never writes config files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trading_system.config.validator import validate_all
from trading_system.result import Err, Ok


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m trading_system.config",
        description="Centralised startup validator for trading-bot YAMLs.",
    )
    parser.add_argument(
        "--validate-all",
        action="store_true",
        help="Drive every shipped loader against its YAML in <dir>.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        help="Directory containing the YAML files (default: ./config).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print successfully-validated and skipped filenames.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.validate_all:
        # Future subcommands land here; for now --validate-all is the
        # only action so reject anything else with a usage hint.
        sys.stderr.write(
            "usage: python -m trading_system.config --validate-all "
            "[--config-dir DIR]\n"
        )
        return 2

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
    # Err branch
    err_report = result.error
    for line in err_report.errors:
        sys.stderr.write(f"{line}\n")
    sys.stderr.write(
        f"config: FAILED ({len(err_report.errors)} error(s)); "
        f"{len(err_report.validated_files)} file(s) validated\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
