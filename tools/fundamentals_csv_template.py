#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Emit a header-only fundamentals CSV stub for a given universe.

The operator runs this once per universe to get a CSV with the right
columns + one row per instrument id. They then fill in the numbers by
hand (or by importing a one-time vendor dump). The
``CSVFundamentalsProvider`` consumes the resulting file.

Usage:
    python tools/fundamentals_csv_template.py \\
        --out data/my_universe_fundamentals.csv \\
        ASML.AS BNP.PA SAN.PA ...

Or from stdin (one instrument id per line):
    cat universe.txt | python tools/fundamentals_csv_template.py \\
        --out data/my_universe_fundamentals.csv

Stdlib only — runs anywhere Python 3.13 runs.

REQ refs: REQ_F_FND_001 (operator workflow).
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The required header set matches ``CSVFundamentalsProvider`` exactly.
HEADER: tuple[str, ...] = (
    "instrument_id",
    "yield_",
    "payout_ratio",
    "free_cash_flow_amount",
    "free_cash_flow_currency",
    "debt_equity",
    "dividend_history_years",
    "as_of_date",
)


def emit(out_path: Path, instrument_ids: list[str]) -> int:
    if not instrument_ids:
        print(
            "error: no instrument ids provided (pass on argv or via stdin)",
            file=sys.stderr,
        )
        return 2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for instrument_id in instrument_ids:
            writer.writerow(
                [
                    instrument_id,
                    "",  # yield_
                    "",  # payout_ratio
                    "",  # free_cash_flow_amount
                    "",  # free_cash_flow_currency
                    "",  # debt_equity
                    "",  # dividend_history_years
                    today,  # as_of_date — operator updates per refresh
                ]
            )
    print(f"wrote {out_path.relative_to(REPO)} ({len(instrument_ids)} rows)")
    return 0


def resolve(p: Path) -> Path:
    return p if p.is_absolute() else REPO / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output CSV path (relative paths resolve against the repo root).",
    )
    ap.add_argument(
        "instrument_ids",
        nargs="*",
        help="Instrument ids; if omitted, read one per line from stdin.",
    )
    args = ap.parse_args(argv)

    if args.instrument_ids:
        ids = list(args.instrument_ids)
    else:
        ids = [line.strip() for line in sys.stdin if line.strip()]

    return emit(resolve(args.out), ids)


if __name__ == "__main__":
    sys.exit(main())
