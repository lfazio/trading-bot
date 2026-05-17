"""Tests for the CR-016 MVP-4 report artefacts (TC_RPT_001..009)."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_system.analytics.report import write_report, report_dir_name
from trading_system.analytics.summary_json import build_summary
from trading_system.analytics.trades_csv import render_trades_csv
from trading_system.backtesting.result import BacktestResult
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.money import Currency, Money
from trading_system.models.rationale import TradeRationale
from trading_system.models.trading import Trade
from trading_system.result import Err, Ok


_NOW = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)


def _trade(
    *,
    tid: str = "t-1",
    oid: str = "o-1",
    at: datetime = _NOW,
    price: str = "100.50",
    qty: str = "10",
    fees: str = "1.50",
) -> Trade:
    return Trade(
        id=TradeId(tid),
        order_id=OrderId(oid),
        executed_at=at,
        price=Decimal(price),
        quantity_filled=Decimal(qty),
        fees=Money(Decimal(fees), Currency.EUR),
    )


def _rationale(
    *, tid: str = "t-1", sid: str = "core_v1", version: str = "1.0"
) -> TradeRationale:
    return TradeRationale(
        trade_id=TradeId(tid),
        strategy_id=StrategyId(sid),
        strategy_version=version,
        signal_reason="yield>4.5",
        risk_approval={},
        tax_gate_decision="",
        improvement_report_id="",
        decided_at=_NOW,
    )


def _empty_result() -> BacktestResult:
    return BacktestResult(
        trades=(),
        equity_curve=(),
        equity_excl_injections=(),
        final_cash=Money(Decimal("1000"), Currency.EUR),
        final_equity_after_tax=Money(Decimal("1000"), Currency.EUR),
        realized_gross=Money(Decimal("0"), Currency.EUR),
        realized_after_tax=Money(Decimal("0"), Currency.EUR),
        dividends_gross=Money(Decimal("0"), Currency.EUR),
        dividends_after_tax=Money(Decimal("0"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
    )


def _result_with_trades(*trades: Trade, rationales: tuple[TradeRationale, ...] = ()) -> BacktestResult:
    curve = (
        EquityPoint(
            at=_NOW,
            equity_gross=Money(Decimal("1000"), Currency.EUR),
            equity_after_tax=Money(Decimal("1000"), Currency.EUR),
            drawdown_pct=Decimal("0"),
        ),
        EquityPoint(
            at=_NOW + timedelta(days=1),
            equity_gross=Money(Decimal("1010"), Currency.EUR),
            equity_after_tax=Money(Decimal("1007"), Currency.EUR),
            drawdown_pct=Decimal("0.005"),
        ),
    )
    return BacktestResult(
        trades=trades,
        equity_curve=curve,
        equity_excl_injections=(Decimal("1000"), Decimal("1007")),
        final_cash=Money(Decimal("995"), Currency.EUR),
        final_equity_after_tax=Money(Decimal("1007"), Currency.EUR),
        realized_gross=Money(Decimal("10"), Currency.EUR),
        realized_after_tax=Money(Decimal("7"), Currency.EUR),
        dividends_gross=Money(Decimal("0"), Currency.EUR),
        dividends_after_tax=Money(Decimal("0"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
        rationales=rationales,
    )


def _write(tmp_path: Path, result: BacktestResult) -> Path:
    out_dir = tmp_path / "report"
    res = write_report(
        result,
        config_hash="cfg-abc",
        out_dir=out_dir,
        seed=42,
        start_at=_NOW,
        end_at=_NOW + timedelta(days=30),
        data_provider="mock",
    )
    assert isinstance(res, Ok), f"write_report returned Err: {res}"
    return out_dir


# ---------------------------------------------------------------------------
# TC_RPT_001 — empty trades ⇒ header-only CSV
# ---------------------------------------------------------------------------


def test_render_trades_csv_empty_returns_header_only() -> None:
    """REQ_F_RPT_002 — `trades.csv` SHALL be UTF-8, comma-separated,
    with the documented header even when no trades occurred."""
    text = render_trades_csv((), ())
    lines = text.strip().split("\n")
    assert len(lines) == 1
    header = lines[0].split(",")
    assert header == [
        "at",
        "trade_id",
        "order_id",
        "price",
        "quantity_filled",
        "fees",
        "fees_currency",
        "slippage",
        "strategy_id",
        "strategy_version",
    ]


# ---------------------------------------------------------------------------
# TC_RPT_002 — chronological-by-(at, trade_id) sort
# ---------------------------------------------------------------------------


def test_render_trades_csv_sorts_chronologically() -> None:
    """REQ_SDD_RPT_002 — row order SHALL be
    ``sorted(trades, key=lambda t: (t.executed_at, t.id))``."""
    t_late = _trade(tid="t-3", at=_NOW + timedelta(hours=2))
    t_mid = _trade(tid="t-2", at=_NOW + timedelta(hours=1))
    t_early = _trade(tid="t-1", at=_NOW)
    # Feed in non-chronological order.
    text = render_trades_csv((t_late, t_early, t_mid))
    rows = text.strip().split("\n")[1:]  # skip header
    ids = [row.split(",")[1] for row in rows]
    assert ids == ["t-1", "t-2", "t-3"]


def test_render_trades_csv_tiebreaks_by_trade_id() -> None:
    """Trades at the same ``at`` SHALL tie-break by trade_id
    lexicographically."""
    t_b = _trade(tid="t-b", at=_NOW)
    t_a = _trade(tid="t-a", at=_NOW)
    text = render_trades_csv((t_b, t_a))
    rows = text.strip().split("\n")[1:]
    ids = [row.split(",")[1] for row in rows]
    assert ids == ["t-a", "t-b"]


# ---------------------------------------------------------------------------
# TC_RPT_003 — Decimal precision + ISO-8601 datetimes
# ---------------------------------------------------------------------------


def test_render_trades_csv_preserves_decimal_precision() -> None:
    t = _trade(qty="1.23456789", price="100.987654321")
    text = render_trades_csv((t,))
    row = text.strip().split("\n")[1]
    cols = row.split(",")
    # cols[3] = price, cols[4] = quantity_filled
    assert cols[3] == "100.987654321"
    assert cols[4] == "1.23456789"


def test_render_trades_csv_datetime_iso_with_tz() -> None:
    text = render_trades_csv((_trade(),))
    row = text.strip().split("\n")[1]
    at = row.split(",")[0]
    assert at.endswith("+00:00")
    assert "T" in at


def test_render_trades_csv_strategy_join_via_rationale() -> None:
    t = _trade(tid="t-1")
    r = _rationale(tid="t-1", sid="core_v2", version="2.1")
    text = render_trades_csv((t,), (r,))
    cols = text.strip().split("\n")[1].split(",")
    assert cols[-2] == "core_v2"
    assert cols[-1] == "2.1"


def test_render_trades_csv_missing_rationale_emits_empty_strings() -> None:
    t = _trade(tid="t-orphan")
    text = render_trades_csv((t,), ())  # no rationales aligned
    cols = text.strip().split("\n")[1].split(",")
    assert cols[-2] == ""
    assert cols[-1] == ""


# ---------------------------------------------------------------------------
# TC_RPT_004 — overwrite protection
# ---------------------------------------------------------------------------


def test_write_report_refuses_to_overwrite_populated_dir(tmp_path: Path) -> None:
    """REQ_SDD_RPT_001 — ``write_report`` SHALL refuse to overwrite
    an existing populated directory, returning a categorised
    ``webui:report_dir_exists:<path>`` Err."""
    out_dir = tmp_path / "report"
    out_dir.mkdir()
    (out_dir / "dummy.txt").write_text("preexisting", encoding="utf-8")
    res = write_report(
        _empty_result(),
        config_hash="x",
        out_dir=out_dir,
        seed=0,
        start_at=_NOW,
        end_at=_NOW,
        data_provider="mock",
    )
    match res:
        case Err(report_err):
            assert report_err.category.startswith("webui:report_dir_exists:")
        case _:
            raise AssertionError("expected Err")


def test_write_report_accepts_empty_existing_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "report"
    out_dir.mkdir()  # empty
    assert isinstance(_write(tmp_path, _empty_result()), Path)


# ---------------------------------------------------------------------------
# TC_RPT_005 — five-file directory
# ---------------------------------------------------------------------------


def test_write_report_emits_five_files(tmp_path: Path) -> None:
    """REQ_SDS_RPT_002 — ``write_report(result, *, config_hash,
    out_dir, seed, start_at, end_at, data_provider)`` emits the
    documented five-file artefact set."""
    out_dir = _write(tmp_path, _empty_result())
    files = {p.name for p in out_dir.iterdir()}
    assert files == {
        "trades.csv",
        "equity-curve.html",
        "equity-curve.png",
        "summary.json",
        "manifest.json",
    }
    # All non-empty.
    for name in files:
        assert (out_dir / name).stat().st_size > 0


# ---------------------------------------------------------------------------
# TC_RPT_006 — manifest 7-key shape + png_sha256
# ---------------------------------------------------------------------------


def test_manifest_has_documented_seven_keys(tmp_path: Path) -> None:
    """REQ_F_RPT_003 / REQ_SDD_RPT_003 — ``manifest.json`` SHALL
    carry exactly the seven documented keys (config_hash, seed,
    start_at, end_at, data_provider, report_schema_version,
    png_sha256)."""
    out_dir = _write(tmp_path, _empty_result())
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert set(manifest.keys()) == {
        "config_hash",
        "seed",
        "start_at",
        "end_at",
        "data_provider",
        "report_schema_version",
        "png_sha256",
    }
    assert manifest["report_schema_version"] == "1"


def test_manifest_png_sha256_matches_png_file(tmp_path: Path) -> None:
    out_dir = _write(tmp_path, _empty_result())
    png_bytes = (out_dir / "equity-curve.png").read_bytes()
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["png_sha256"] == hashlib.sha256(png_bytes).hexdigest()


# ---------------------------------------------------------------------------
# TC_RPT_007 — byte-identical replay
# ---------------------------------------------------------------------------


def test_byte_identical_replay_trades_summary_manifest(tmp_path: Path) -> None:
    """REQ_NF_RPT_001 — two write_report calls with identical inputs
    produce byte-identical trades.csv + summary.json + manifest.json.

    The PNG pixel determinism is best-effort under the same matplotlib
    version; the test verifies the manifest's png_sha256 round-trips
    identically on the same host."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    result = _empty_result()
    write_report(
        result,
        config_hash="abc",
        out_dir=a,
        seed=42,
        start_at=_NOW,
        end_at=_NOW + timedelta(days=30),
        data_provider="mock",
    ).unwrap()
    write_report(
        result,
        config_hash="abc",
        out_dir=b,
        seed=42,
        start_at=_NOW,
        end_at=_NOW + timedelta(days=30),
        data_provider="mock",
    ).unwrap()
    # trades.csv / summary.json / manifest.json byte-identical.
    for name in ("trades.csv", "summary.json", "manifest.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), (
            f"{name} differs between two writes"
        )


# ---------------------------------------------------------------------------
# TC_RPT_008 — HTML round-trip; no JS / network deps
# ---------------------------------------------------------------------------


def test_equity_curve_html_embeds_png_base64(tmp_path: Path) -> None:
    out_dir = _write(tmp_path, _empty_result())
    html = (out_dir / "equity-curve.html").read_text(encoding="utf-8")
    png_bytes = (out_dir / "equity-curve.png").read_bytes()
    expected_b64 = base64.b64encode(png_bytes).decode("ascii")
    assert expected_b64 in html
    # Decoding the base64 back ⇒ same PNG bytes.
    start = html.index("base64,") + len("base64,")
    end = html.index('"', start)
    assert base64.b64decode(html[start:end]) == png_bytes


def test_equity_curve_html_has_no_javascript_or_network_refs(tmp_path: Path) -> None:
    out_dir = _write(tmp_path, _empty_result())
    html = (out_dir / "equity-curve.html").read_text(encoding="utf-8").lower()
    # No script tags / src URLs / external CSS imports.
    assert "<script" not in html
    assert "</script" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "@import" not in html


# ---------------------------------------------------------------------------
# TC_RPT_009 — import-graph audit
# ---------------------------------------------------------------------------


def test_report_module_does_not_reach_decisioning_layers() -> None:
    """REQ_SDS_RPT_001 — analytics/report.py + siblings SHALL NOT
    import execution / safety / strategy_lab / risk / webui."""
    import ast

    repo_root = Path(__file__).resolve().parent.parent.parent
    analytics_dir = repo_root / "trading_system" / "analytics"
    forbidden_prefixes = (
        "trading_system.execution",
        "trading_system.safety",
        "trading_system.strategy_lab",
        "trading_system.risk",
        "trading_system.webui",
    )
    # Only the new report modules + analytics-package members the
    # report relies on are audited. The pre-existing analytics/engine.py
    # is more permissive (touches other modules); we audit the new
    # files only.
    new_files = (
        "report.py",
        "trades_csv.py",
        "equity_chart.py",
        "summary_json.py",
        "manifest_json.py",
    )
    for name in new_files:
        py_file = analytics_dir / name
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"{name} imports {module} — REQ_SDS_RPT_001"
                    )


# ---------------------------------------------------------------------------
# report_dir_name helper
# ---------------------------------------------------------------------------


def test_report_dir_name_has_filesystem_safe_chars() -> None:
    name = report_dir_name(_NOW)
    # No colons (Windows-hostile); no '+' (URL-meaningful).
    assert ":" not in name
    assert "+" not in name
    # Contains date + "T" separator + Z for the timezone.
    assert name.startswith("2024-01-02")


# ---------------------------------------------------------------------------
# summary_json content shape
# ---------------------------------------------------------------------------


def test_summary_carries_dashboard_fields(tmp_path: Path) -> None:
    out_dir = _write(tmp_path, _empty_result())
    summary = json.loads((out_dir / "summary.json").read_text())
    expected = {
        "trades_count",
        "knockouts",
        "injections_applied",
        "currency",
        "final_cash",
        "final_equity_after_tax",
        "realized_gross",
        "realized_after_tax",
        "dividends_gross",
        "dividends_after_tax",
        "max_drawdown",
        "equity_curve_points",
    }
    assert set(summary.keys()) == expected


def test_summary_max_drawdown_from_curve() -> None:
    """REQ_F_RPT_001 — max_drawdown is the maximum across the curve."""
    result = _result_with_trades()
    s = build_summary(result)
    # The curve was constructed with drawdowns 0 and 0.005 — max is 0.005.
    assert s["max_drawdown"] == Decimal("0.005")


# ---------------------------------------------------------------------------
# Trade-count flow
# ---------------------------------------------------------------------------


def test_report_with_trades_writes_csv_rows(tmp_path: Path) -> None:
    t = _trade(tid="t-1")
    r = _rationale(tid="t-1")
    result = _result_with_trades(t, rationales=(r,))
    out_dir = _write(tmp_path, result)
    csv_lines = (out_dir / "trades.csv").read_text().strip().split("\n")
    assert len(csv_lines) == 2  # header + 1 trade row
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["trades_count"] == 1
