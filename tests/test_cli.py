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
    5-file report directory at the requested path."""
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
