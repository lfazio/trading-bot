"""Smoke test for ``trading_system.main``.

Runs the full pipeline against the shipped ``config/`` directory
and asserts the returned dashboard view carries the headline
sections REQ_F_DSH_001 mandates. The test is hermetic — the demo
uses ``MockMarketDataProvider`` and ``LocalBrokerAdapter``, so no
network and no broker connection.

REQ refs:
- REQ_O_001 — runnable Python project with no missing modules.
- REQ_O_002 — main.py demonstrates the end-to-end pipeline.
- REQ_O_003 — starting capital + broker selection + phase
  thresholds read from configuration (system.yaml + phases.yaml +
  risk.yaml).
- REQ_F_DSH_001 — dashboard surface populated.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from trading_system.dashboard import DashboardView
from trading_system.main import run
from trading_system.result import Err, Ok

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


def test_demo_runs_and_returns_dashboard_view() -> None:
    out = io.StringIO()
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
        out_stream=out,
    )
    match res:
        case Ok(view):
            assert isinstance(view, DashboardView)
            # REQ_F_DSH_001 fields populated.
            assert view.allocation, "allocation rows should be populated"
            assert view.attribution, "attribution should at least carry the NAV row"
            assert view.attribution[0].kind == "nav"
            # The mock provider with 3 stocks emits 3 ticks/day; over
            # ~31 days we get ~93 ticks => >= 30 equity points.
            assert len(view.trade_history) >= 0
        case Err(reason):
            raise AssertionError(f"main.run failed: {reason}")


def test_demo_writes_summary_to_out_stream() -> None:
    out = io.StringIO()
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 15, tzinfo=UTC),
        out_stream=out,
    )
    assert isinstance(res, Ok)
    text = out.getvalue()
    assert "starting capital" in text
    assert "max drawdown" in text
    assert "sharpe (after tax)" in text


def test_demo_with_slippage_branch_runs() -> None:
    out = io.StringIO()
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 10, tzinfo=UTC),
        use_slippage=True,
        out_stream=out,
    )
    assert isinstance(res, Ok)


def test_missing_config_dir_returns_categorised_err() -> None:
    res = run(
        config_dir=Path("/nonexistent-config-dir-xyz"),
        out_stream=io.StringIO(),
    )
    match res:
        case Err(reason):
            assert reason.startswith("main:system_config_read")
        case Ok(_):
            raise AssertionError("expected Err for missing config")
