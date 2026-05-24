"""``_default_worker`` SHALL emit ``attribution.json`` next to the
CR-016 5-file bundle so the reports view can surface per-strategy
attribution without re-running the backtest.

The worker spawns a process pool internally; this test exercises
the worker function directly to avoid the multi-process round-
trip + assert the side-file lands on disk.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from trading_system.webapp.job_queue import JobSpec, _default_worker


def test_worker_writes_attribution_json_alongside_5_file_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    """Build a self-contained config dir pointing at the bundled
    ``eu-dividend-starter`` fixtures so the test doesn't depend on
    the operator-tuned global ``config/system.yaml`` (which CR-021
    re-pointed at the local CAC 40 cache)."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_config = repo_root / "config"
    test_config = tmp_path / "config"
    test_config.mkdir(parents=True)
    # Copy the global config files unchanged…
    for yaml_path in src_config.glob("*.yaml"):
        (test_config / yaml_path.name).write_text(
            yaml_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    # …then override system.yaml to target the bundled fixtures so
    # the worker's 5-file bundle generation has data to work with.
    (test_config / "system.yaml").write_text(
        """system:
  starting_capital:
    amount: 1000
    currency: EUR
  log_level: INFO
  seed: 0xCAFE
  mode: backtest
broker:
  adapter: local
data:
  provider: yfinance
  cache_root: .cache/yfinance
  bundled_fixtures: true
  universe: eu-dividend-starter
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    spec = JobSpec(
        job_id="job-attr-test",
        config_dir=str(test_config),
        start=datetime(2024, 1, 2, tzinfo=UTC),
        end=datetime(2024, 12, 31, tzinfo=UTC),
        with_slippage=False,
        account_id="default",
    )
    summary = _default_worker(spec)
    report_dir = Path(summary["report_dir"])
    # The 5-file bundle landed.
    for name in (
        "trades.csv",
        "equity-curve.html",
        "equity-curve.png",
        "summary.json",
        "manifest.json",
    ):
        assert (report_dir / name).is_file(), f"missing {name}"
    # The Phase-6 side-file also landed.
    attr_path = report_dir / "attribution.json"
    assert attr_path.is_file(), "attribution.json missing"
    attr = json.loads(attr_path.read_text(encoding="utf-8"))
    # Shape: portfolio totals + by_strategy list.
    assert "currency" in attr
    assert "portfolio_trade_count" in attr
    assert "portfolio_turnover" in attr
    assert "portfolio_fees" in attr
    assert "portfolio_realized_pnl" in attr
    assert isinstance(attr["by_strategy"], list)
    # JSON SHALL be canonical (sorted keys + tight separators) so
    # operator tooling can diff two runs side-by-side.
    raw = attr_path.read_text(encoding="utf-8")
    assert raw == json.dumps(attr, sort_keys=True, separators=(",", ":"))
