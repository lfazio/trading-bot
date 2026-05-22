"""Fundamentals seed-CSV coverage — REQ_TP_FIX_002.

REQ_TP_FIX_002 — Fundamentals fixtures SHALL cover ≥ 50 EU
equities spanning the sectors and yield bands referenced by
REQ_F_SCR_001 (dividend yield 3–7 %, payout ratio < 70 %, FCF > 0,
debt/equity < 1.5, ≥ 5 years dividend history).

The audit reads the shipped ``data/seed_fundamentals.csv`` and
asserts:

  1. Row count ≥ 50.
  2. Coverage of the 3–7 % yield band — at least 20 rows fall
     inside the screener's yield window.
  3. Coverage of EU exchanges — at least 5 distinct exchange
     suffixes among the instrument ids (the EU equity universe
     spans PA / AS / DE / SW / L / MI / MC).
  4. Coverage of currency mix — at least 3 distinct ISO codes
     (EUR / CHF / GBP minimum).
  5. Schema is valid — every row parses with the documented
     columns + non-negative FCF.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SEED_CSV = _REPO_ROOT / "data" / "seed_fundamentals.csv"


def _rows() -> list[dict[str, str]]:
    with _SEED_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_seed_csv_has_at_least_50_eu_equities() -> None:
    """REQ_TP_FIX_002 — ≥ 50 rows."""
    rows = _rows()
    assert len(rows) >= 50, f"seed has {len(rows)} rows, need ≥ 50"


def test_seed_csv_covers_screener_yield_band() -> None:
    """REQ_TP_FIX_002 + REQ_F_SCR_001 — at least 20 rows SHALL
    fall inside the screener's 3–7 % yield band so screener
    tests have meaningful happy-path coverage. Rows outside the
    band exercise the rejection path."""
    rows = _rows()
    in_band = [
        r for r in rows
        if Decimal("0.03") <= Decimal(r["yield_"]) <= Decimal("0.07")
    ]
    assert len(in_band) >= 20, (
        f"only {len(in_band)} rows in the 3–7 % yield band; need ≥ 20"
    )


def test_seed_csv_spans_multiple_eu_exchanges() -> None:
    """REQ_TP_FIX_002 — EU equity universe SHALL span multiple
    exchange suffixes (PA / AS / DE / SW / L / MI / MC). Audit
    requires at least 5 distinct suffixes."""
    rows = _rows()
    suffixes = {
        r["instrument_id"].rsplit(".", 1)[1]
        for r in rows
        if "." in r["instrument_id"]
    }
    assert len(suffixes) >= 5, (
        f"only {len(suffixes)} distinct exchange suffixes; need ≥ 5: {suffixes}"
    )


def test_seed_csv_spans_multiple_currencies() -> None:
    """REQ_TP_FIX_002 — EU equity universe SHALL include at least
    3 currencies (EUR / CHF / GBP minimum; CR-011 FX hedger
    tests need a multi-currency universe)."""
    rows = _rows()
    currencies = {r["free_cash_flow_currency"] for r in rows}
    assert len(currencies) >= 3, (
        f"only {len(currencies)} currencies in seed; need ≥ 3: {currencies}"
    )


# ---------------------------------------------------------------------------
# Schema validity — every row parses cleanly
# ---------------------------------------------------------------------------


def test_seed_csv_rows_parse_cleanly() -> None:
    """REQ_F_SCR_001 family — every row's numeric fields parse
    as Decimal; FCF > 0; payout_ratio + yield_ + debt_equity in
    plausible ranges. A bad row would fail the
    CSVFundamentalsProvider at boot time."""
    rows = _rows()
    for r in rows:
        # Numeric coercion succeeds.
        y = Decimal(r["yield_"])
        p = Decimal(r["payout_ratio"])
        fcf = Decimal(r["free_cash_flow_amount"])
        de = Decimal(r["debt_equity"])
        years = int(r["dividend_history_years"])
        # Plausible ranges.
        assert Decimal(0) <= y <= Decimal("0.20"), f"{r['instrument_id']}: y={y}"
        assert Decimal(0) <= p <= Decimal("1.0"), f"{r['instrument_id']}: p={p}"
        assert fcf > 0, f"{r['instrument_id']}: FCF must be > 0"
        assert Decimal(0) <= de <= Decimal("3.0"), f"{r['instrument_id']}: de={de}"
        assert years >= 1
