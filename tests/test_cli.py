"""Tests for ``trading_system.cli`` (TC_CLI_001..005)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from trading_system.cli import _build_parser, main


# ---------------------------------------------------------------------------
# TC_CLI_001 — validate-config happy + bad-dir paths
# ---------------------------------------------------------------------------


def test_validate_config_happy_path(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["validate-config"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "config: OK" in captured.out


def test_validate_config_verbose_flag(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["validate-config", "-v"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "validated:" in captured.out
    assert "system.yaml" in captured.out


def test_validate_config_bad_dir_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    not_a_config_dir = tmp_path / "no-such-dir"
    exit_code = main(["validate-config", "--config-dir", str(not_a_config_dir)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "config: FAILED" in captured.err


def test_validate_config_rich_errors_flag_clean(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """C3 — `--rich-errors` runs the Pydantic v2 schemas
    alongside the existing typed loaders. Clean config dir ⇒
    both reports print OK."""
    exit_code = main(["validate-config", "--rich-errors"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "config: OK" in captured.out
    assert "config (rich): OK" in captured.out


def test_list_backtests_db_missing_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C10 — non-existent db path ⇒ exit 1 + categorised error."""
    fake_db = tmp_path / "no-such.sqlite"
    exit_code = main(["list-backtests", "--db", str(fake_db)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "db not found" in captured.err


def _seed_db_with_backtest(tmp_path: Path) -> Path:
    """Build a SQLite db with one archived backtest row + return the
    db path. Used by the C10 CLI tests."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from trading_system.backtesting.result import BacktestResult
    from trading_system.models.flow import EquityPoint
    from trading_system.models.identifiers import OrderId, StrategyId, TradeId
    from trading_system.models.money import Currency, Money
    from trading_system.models.trading import Trade
    from trading_system.persistence.connection import Connection
    from trading_system.persistence.migrations.runner import MigrationRunner
    from trading_system.persistence.repositories.backtest import (
        BacktestResultRepository,
    )

    db_path = tmp_path / "state.sqlite"
    conn = Connection.open(db_path).unwrap()
    repo_root = Path(__file__).resolve().parent.parent
    MigrationRunner(
        conn=conn,
        migrations_dir=repo_root / "trading_system" / "persistence" / "migrations",
    ).run()
    result = BacktestResult(
        trades=(
            Trade(
                id=TradeId("t-1"),
                order_id=OrderId("o-1"),
                executed_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
                price=Decimal("100"),
                quantity_filled=Decimal("10"),
                fees=Money(Decimal("1.00"), Currency.EUR),
                slippage=Decimal("0"),
            ),
        ),
        equity_curve=(
            EquityPoint(
                at=datetime(2026, 5, 8, tzinfo=UTC),
                equity_gross=Money(Decimal("10100"), Currency.EUR),
                equity_after_tax=Money(Decimal("10000"), Currency.EUR),
                drawdown_pct=Decimal("0.05"),
            ),
        ),
        equity_excl_injections=(Decimal("10000"),),
        final_cash=Money(Decimal("500"), Currency.EUR),
        final_equity_after_tax=Money(Decimal("11500"), Currency.EUR),
        realized_gross=Money(Decimal("1000"), Currency.EUR),
        realized_after_tax=Money(Decimal("700"), Currency.EUR),
        dividends_gross=Money(Decimal("50"), Currency.EUR),
        dividends_after_tax=Money(Decimal("35"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
    )
    BacktestResultRepository(conn=conn).archive(
        result,
        strategy_id=StrategyId("alpha"),
        git_sha="sha1",
        config_hash="cfg1",
        seed=42,
    )
    conn.close()
    return db_path


def test_list_backtests_emits_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _seed_db_with_backtest(tmp_path)
    exit_code = main(["list-backtests", "--db", str(db)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "11500" in out
    assert "1 row(s) (of 1 archived)" in out


def test_list_backtests_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as _json

    db = _seed_db_with_backtest(tmp_path)
    exit_code = main(["list-backtests", "--db", str(db), "--json"])
    assert exit_code == 0
    payload = _json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["strategy_id"] == "alpha"
    assert payload[0]["seed"] == 42
    assert payload[0]["final_equity"] == "11500"
    assert payload[0]["trades_count"] == 1


def test_list_backtests_metric_filter_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _seed_db_with_backtest(tmp_path)
    exit_code = main(
        [
            "list-backtests",
            "--db",
            str(db),
            "--metric",
            "final_equity>10000",
            "--json",
        ]
    )
    assert exit_code == 0
    import json as _json

    rows = _json.loads(capsys.readouterr().out)
    assert len(rows) == 1


def test_list_backtests_metric_filter_excludes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Metric expression that doesn't match SHALL surface zero rows."""
    db = _seed_db_with_backtest(tmp_path)
    exit_code = main(
        [
            "list-backtests",
            "--db",
            str(db),
            "--metric",
            "final_equity<5000",
            "--json",
        ]
    )
    assert exit_code == 0
    import json as _json

    rows = _json.loads(capsys.readouterr().out)
    assert rows == []


def test_list_backtests_multiple_metric_filters_AND(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Multiple --metric flags AND together."""
    db = _seed_db_with_backtest(tmp_path)
    # Both pass: final_equity > 10000 AND max_drawdown < 0.1
    exit_code = main(
        [
            "list-backtests",
            "--db",
            str(db),
            "--metric",
            "final_equity>10000",
            "--metric",
            "max_drawdown<0.1",
            "--json",
        ]
    )
    assert exit_code == 0
    import json as _json

    rows = _json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    # One filter passes, one fails ⇒ row excluded.
    exit_code = main(
        [
            "list-backtests",
            "--db",
            str(db),
            "--metric",
            "final_equity>10000",
            "--metric",
            "max_drawdown>1.0",
            "--json",
        ]
    )
    assert exit_code == 0
    rows = _json.loads(capsys.readouterr().out)
    assert rows == []


def test_list_backtests_invalid_metric_expression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _seed_db_with_backtest(tmp_path)
    # No operator.
    exit_code = main(
        ["list-backtests", "--db", str(db), "--metric", "final_equity"]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "no_op" in err


def test_list_backtests_unknown_metric_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _seed_db_with_backtest(tmp_path)
    exit_code = main(
        ["list-backtests", "--db", str(db), "--metric", "sharpe>1.0"]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "unknown_name" in err


def test_list_backtests_filters_by_strategy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _seed_db_with_backtest(tmp_path)
    # Filter by 'alpha' (matches the seeded row).
    exit_code = main(
        ["list-backtests", "--db", str(db), "--strategy", "alpha", "--json"]
    )
    assert exit_code == 0
    import json as _json

    assert len(_json.loads(capsys.readouterr().out)) == 1
    # Filter by 'beta' (no match).
    exit_code = main(
        ["list-backtests", "--db", str(db), "--strategy", "beta", "--json"]
    )
    assert exit_code == 0
    assert _json.loads(capsys.readouterr().out) == []


def test_validate_config_rich_errors_flag_surfaces_field_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C3 — `--rich-errors` against a broken notifications.yaml
    prints every field-level violation in a single tree-shaped
    report. Single Pydantic pass collects multiple field errors."""
    # Copy the bundled config + clobber notifications.yaml with a
    # broken version so the rest of the loaders still pass.
    import shutil

    repo_root = Path(__file__).resolve().parent.parent
    bundled = repo_root / "config"
    dst = tmp_path / "config"
    shutil.copytree(bundled, dst)
    (dst / "notifications.yaml").write_text(
        """
notifications:
  channels: [unknown_channel]
  retry:
    max_attempts: 0
    base_delay_seconds: -1
""",
        encoding="utf-8",
    )
    exit_code = main(
        ["validate-config", "--config-dir", str(dst), "--rich-errors"]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    # Tree shape: file header followed by indented field lines.
    assert "notifications.yaml:" in captured.err
    assert "notifications.retry.max_attempts" in captured.err
    assert "notifications.retry.base_delay_seconds" in captured.err
    assert "notifications.channels" in captured.err


# ---------------------------------------------------------------------------
# TC_CLI_002 — backtest argparse
# ---------------------------------------------------------------------------


def test_backtest_missing_optional_args_runs() -> None:
    """All ``backtest`` flags have defaults — running with no args
    after the subcommand SHALL parse cleanly."""
    parser = _build_parser()
    args = parser.parse_args(["backtest"])
    assert args.cmd == "backtest"
    assert args.with_slippage is False
    assert args.report_dir is None


def test_backtest_unknown_flag_exits_two() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["backtest", "--bogus"])
    assert exc.value.code == 2


def test_backtest_with_slippage_flag_parses() -> None:
    parser = _build_parser()
    args = parser.parse_args(["backtest", "--with-slippage"])
    assert args.with_slippage is True


def test_backtest_subcommand_emits_report_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CR-016 Phase B — ``main(['backtest', '--report-dir', ...])``
    exercises the full dispatcher path against the shipped config
    + the bundled fixtures + the mock provider and SHALL emit the
    5-file report directory at the requested path.

    Skipped when ``plotly`` + ``kaleido`` (the ``[reports]`` extra)
    aren't installed — the renderer needs them (CR-020); envs
    without the extra (slim webapp container) skip cleanly rather
    than fail on import. Install with
    ``pip install -e .[reports]`` to run."""
    pytest.importorskip("plotly")
    pytest.importorskip("kaleido")
    report_dir = tmp_path / "report"
    exit_code = main(["backtest", "--report-dir", str(report_dir)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "trading-bot backtest: OK" in captured.out
    assert str(report_dir) in captured.out
    # All 5 MVP-4 artefacts land on disk.
    assert (report_dir / "trades.csv").is_file()
    assert (report_dir / "equity-curve.html").is_file()
    assert (report_dir / "equity-curve.png").is_file()
    assert (report_dir / "summary.json").is_file()
    assert (report_dir / "manifest.json").is_file()


# ---------------------------------------------------------------------------
# TC_CLI_003 — record-data delegates to recorder
# ---------------------------------------------------------------------------


def test_record_data_missing_universe_exits_two() -> None:
    """``--universe`` is required for ``record-data``."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["record-data", "--start", "2023-01-01", "--end", "2024-01-01"])
    assert exc.value.code == 2


def test_record_data_unknown_universe_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "record-data",
            "--universe",
            "ghost-universe-does-not-exist",
            "--start",
            "2023-01-01",
            "--end",
            "2024-01-01",
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# TC_CLI_004 — bad / missing subcommand exits non-zero
# ---------------------------------------------------------------------------


def test_no_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_unknown_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus-command"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# TC_CLI_005 — console-script registration + import-graph audit
# ---------------------------------------------------------------------------


def test_console_script_registered_in_pyproject() -> None:
    """REQ_O_004 / REQ_SDS_CLI_002 — the package SHALL register a
    ``trading-bot`` console script via ``pyproject.toml``'s
    ``[project.scripts]`` table, mapped to
    ``trading_system.cli:main``."""
    repo_root = Path(__file__).resolve().parent.parent
    with (repo_root / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("trading-bot") == "trading_system.cli:main"


def test_cli_does_not_import_decisioning_modules_directly() -> None:
    """REQ_SDS_CLI_001 / REQ_SDD_CLI_002 plumbing-only invariant —
    ``trading_system/cli.py`` SHALL NOT import ``execution.*`` /
    ``safety.*`` / ``risk.*`` / ``strategy_lab.*`` directly. The CLI
    reaches those modules transitively through ``main.run`` and
    other public entry points only."""
    import ast

    repo_root = Path(__file__).resolve().parent.parent
    cli_path = repo_root / "trading_system" / "cli.py"
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    forbidden = (
        "trading_system.execution",
        "trading_system.safety",
        "trading_system.risk",
        "trading_system.strategy_lab",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for prefix in forbidden:
                assert not module.startswith(prefix), (
                    f"cli.py imports {module} — REQ_SDS_CLI_001 plumbing-only"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden:
                    assert not alias.name.startswith(prefix), (
                        f"cli.py imports {alias.name} — "
                        "REQ_SDS_CLI_001 plumbing-only"
                    )


def test_handlers_return_int_no_sys_exit() -> None:
    """REQ_SDD_CLI_001 — handlers SHALL ``return`` the exit code,
    not call ``sys.exit`` directly. A quick AST audit catches
    accidental sys.exit() calls inside handler functions."""
    import ast

    repo_root = Path(__file__).resolve().parent.parent
    cli_path = repo_root / "trading_system" / "cli.py"
    tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    for node in ast.walk(tree):
        # Look at handler functions only.
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_run_"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    if (
                        isinstance(inner.func, ast.Attribute)
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == "sys"
                        and inner.func.attr == "exit"
                    ):
                        raise AssertionError(
                            f"{node.name} calls sys.exit — handlers SHALL "
                            "return the exit code (REQ_SDD_CLI_001)"
                        )


# ---------------------------------------------------------------------------
# Top-level main entry point shape
# ---------------------------------------------------------------------------


def test_main_returns_int_not_none() -> None:
    """``main`` SHALL return an int so the ``[project.scripts]``
    entry point (``sys.exit(main())``) gets a clean exit code."""
    code = main(["validate-config"])
    assert isinstance(code, int)


def test_main_argv_can_be_passed_explicitly() -> None:
    """Tests + the console script both call ``main(argv)`` with an
    explicit list; passing ``None`` falls back to ``sys.argv``."""
    code = main(argv=["validate-config"])
    assert code == 0


# ---------------------------------------------------------------------------
# CR-024 / TC_OPS_001 — issue-token subcommand
# ---------------------------------------------------------------------------


def test_issue_token_happy_path(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ_F_TOK_005 — `trading-bot issue-token` reads the secret
    from the configured env var + emits one line on stdout."""
    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke-secret" * 4)
    exit_code = main(
        ["issue-token", "--account-id", "default", "--ttl", "60"]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    token = captured.out.strip()
    # CR-024 four-segment format.
    parts = token.rsplit(":", 3)
    assert len(parts) == 4


def test_issue_token_missing_secret_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent env var SHALL exit 1 with a categorised stderr."""
    monkeypatch.delenv("TRADING_BOT_OPERATOR_SECRET", raising=False)
    exit_code = main(["issue-token", "--account-id", "default"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "TRADING_BOT_OPERATOR_SECRET" in captured.err
    assert captured.out.strip() == ""


def test_issue_token_no_secret_argv_flag() -> None:
    """REQ_F_TOK_005 / REQ_SDD_TOK_005 — raw secrets SHALL NEVER
    appear in argv. The argparse subparser SHALL NOT carry a
    `--secret <hex>` flag."""
    parser = _build_parser()
    # Drill into the issue-token subparser to enumerate its
    # arguments.
    subparsers_action = next(
        a for a in parser._subparsers._group_actions  # type: ignore[attr-defined]
        if hasattr(a, "choices")
    )
    issue_parser = subparsers_action.choices["issue-token"]
    option_strings: list[str] = []
    for action in issue_parser._actions:
        option_strings.extend(action.option_strings)
    assert "--secret" not in option_strings
    # The env-var indirection is allowed (just the variable NAME).
    assert "--secret-env" in option_strings


def test_issue_token_custom_secret_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI honours `--secret-env <name>` so operators can name
    their own env var (helpful when integrating with a secret
    manager)."""
    monkeypatch.setenv("MY_TOKEN_SECRET", "alt-secret-value" * 4)
    exit_code = main(
        [
            "issue-token",
            "--account-id",
            "alpha",
            "--secret-env",
            "MY_TOKEN_SECRET",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_issue_token_rejects_non_positive_ttl(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Argparse keeps the ttl as int; the handler defensive-checks
    > 0."""
    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke-secret" * 4)
    exit_code = main(
        ["issue-token", "--account-id", "default", "--ttl", "0"]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "--ttl" in captured.err


# ---------------------------------------------------------------------------
# CR-019 step 2 / TC_OPS_LIV_001 — live-preflight subcommand smoke
# ---------------------------------------------------------------------------


def test_live_preflight_subcommand_registered() -> None:
    """REQ_F_LIV_005 — the `live-preflight` subcommand SHALL be
    registered with the documented argument shape."""
    parser = _build_parser()
    subparsers_action = next(
        a for a in parser._subparsers._group_actions  # type: ignore[attr-defined]
        if hasattr(a, "choices")
    )
    assert "live-preflight" in subparsers_action.choices
    lp_parser = subparsers_action.choices["live-preflight"]
    option_strings: list[str] = []
    for action in lp_parser._actions:
        option_strings.extend(action.option_strings)
    assert "--out" in option_strings
    assert "--config-dir" in option_strings


def test_live_preflight_paper_selector_accepted_at_first_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """REQ_F_PAP_013 / REQ_F_PAP_014 / REQ_SDD_PAP_004 /
    REQ_SDD_PAP_005 / TC_PAP_BRK_005 / TC_PAP_BRK_006 —
    ``broker.adapter: paper`` SHALL load cleanly from
    ``system.yaml`` (config loader accepts the selector) AND pass
    the ``broker_selector`` gate AND the ``broker_authenticate``
    gate (PaperBrokerAdapter has no auth surface). Subsequent gates
    may still fail in a dev-box environment without preflight
    artefacts; the test asserts the first two gates pass."""
    import json as _json

    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke-secret" * 4)
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).resolve().parent.parent
    src_config = repo_root / "config"
    test_config = tmp_path / "config"
    test_config.mkdir()
    for yaml_path in src_config.glob("*.yaml"):
        (test_config / yaml_path.name).write_text(
            yaml_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    (test_config / "system.yaml").write_text(
        """system:
  starting_capital:
    amount: 10000
    currency: EUR
  log_level: INFO
  seed: 0xCAFE
  mode: paper
broker:
  adapter: paper
""",
        encoding="utf-8",
    )
    out = tmp_path / "var" / "preflight.json"
    main(
        [
            "live-preflight",
            "--config-dir",
            str(test_config),
            "--out",
            str(out),
        ]
    )
    # The artefact lands regardless of overall outcome.
    assert out.is_file()
    payload = _json.loads(out.read_text(encoding="utf-8"))
    # Gate 0 (broker_selector) accepts "paper".
    assert payload["gates"][0]["name"] == "broker_selector"
    assert payload["gates"][0]["outcome"] == "ok"
    # Gate 1 (broker_authenticate) passes since paper has no auth.
    assert payload["gates"][1]["name"] == "broker_authenticate"
    assert payload["gates"][1]["outcome"] == "ok"


def test_live_preflight_against_local_broker_fails_first_gate(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When `config/system.yaml` declares `broker.adapter: local`
    (the default in this repo), the preflight SHALL fail the
    broker_selector gate FIRST with exit code 1."""
    import json as _json

    monkeypatch.setenv("TRADING_BOT_OPERATOR_SECRET", "smoke-secret" * 4)
    monkeypatch.chdir(tmp_path)
    repo_root = Path(__file__).resolve().parent.parent
    src_config = repo_root / "config"
    test_config = tmp_path / "config"
    test_config.mkdir()
    for yaml_path in src_config.glob("*.yaml"):
        (test_config / yaml_path.name).write_text(
            yaml_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    # Force broker.adapter to 'local' so the first gate fails.
    sys_yaml = test_config / "system.yaml"
    sys_yaml.write_text(
        """system:
  starting_capital:
    amount: 1000
    currency: EUR
  log_level: INFO
  seed: 0xCAFE
  mode: backtest
broker:
  adapter: local
""",
        encoding="utf-8",
    )
    out = tmp_path / "var" / "preflight.json"
    exit_code = main(
        [
            "live-preflight",
            "--config-dir",
            str(test_config),
            "--out",
            str(out),
        ]
    )
    assert exit_code == 1
    assert out.is_file()
    payload = _json.loads(out.read_text(encoding="utf-8"))
    assert payload["outcome"] == "failed"
    assert payload["gates"][0]["name"] == "broker_selector"
    assert payload["gates"][0]["outcome"] == "failed"
