"""C3 — Pydantic v2 schema validation tests.

REQ refs: REQ_SDS_CFG_001 (validated at startup),
REQ_SDS_CFG_002 (absent file ⇒ defaults), REQ_SDD_ERR_002
(categorised Errs preserved through the rich path).
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.config.pydantic_schemas import (
    AccountsYAML,
    FieldValidationOutcome,
    KillSwitchYAML,
    LoggingYAML,
    MCDrawdownFloorYAML,
    NotificationsYAML,
    PhasesYAML,
    QuantYAML,
    RichValidationReport,
    RiskYAML,
    SystemYAML,
    TurbosYAML,
    WebUIYAML,
    render_rich_report,
    validate_with_pydantic_schemas,
)
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_CONFIG_DIR = _REPO_ROOT / "config"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def seeded_config_dir(tmp_path: Path) -> Path:
    """Copy the bundled required YAMLs into ``tmp_path`` so
    per-YAML tests don't accidentally fail on missing-required-file
    errors from OTHER schemas in `RICH_SCHEMAS`.

    Required YAMLs: risk.yaml, kill_switch.yaml, system.yaml,
    turbos.yaml. Optional YAMLs (notifications, mc_drawdown_floor,
    webui, logging) are NOT copied — the per-test fixture writes
    them under the file under test so absent / empty / invalid
    cases are clean inputs.
    """
    for required in (
        "risk.yaml",
        "kill_switch.yaml",
        "system.yaml",
        "turbos.yaml",
        "phases.yaml",
    ):
        shutil.copy(_BUNDLED_CONFIG_DIR / required, tmp_path / required)
    return tmp_path


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_absent_notifications_yaml_skipped(seeded_config_dir: Path) -> None:
    """No notifications.yaml ⇒ skipped (optional). Report stays Ok."""
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Ok), result
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.skipped_files


def test_empty_notifications_yaml_validates(seeded_config_dir: Path) -> None:
    """Empty file ⇒ Pydantic uses defaults (the documented
    absent-section-defaults behaviour)."""
    _write(seeded_config_dir, "notifications.yaml", "")
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Ok), result
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.validated_files


def test_full_valid_notifications_yaml(seeded_config_dir: Path) -> None:
    _write(
        seeded_config_dir,
        "notifications.yaml",
        """
notifications:
  channels:
    - local_log
    - slack
  retry:
    max_attempts: 5
    base_delay_seconds: 0.1
    growth_factor: 1.5
  approval:
    timeout_seconds: 30
    threshold_amount: "100.00"
    threshold_currency: EUR
  local_log_path: var/logs/notifications.jsonl
  slack:
    webhook_url_env: CUSTOM_SLACK_ENV
    timeout_seconds: 3.0
""",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Ok), result
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.validated_files


def test_email_channel_with_full_settings(seeded_config_dir: Path) -> None:
    _write(
        seeded_config_dir,
        "notifications.yaml",
        """
notifications:
  channels: [email]
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    user: alerts@example.com
    from_addr: alerts@example.com
    recipients:
      - operator@example.com
""",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Ok), result


# ---------------------------------------------------------------------------
# Error collection — every issue surfaces in one pass
# ---------------------------------------------------------------------------


def test_multiple_field_errors_collected_in_one_pass(tmp_path: Path) -> None:
    """Pydantic's hallmark: every field-level violation surfaces
    in a single validation pass. The existing first-error-stops
    loader path only shows one of these at a time."""
    _write(
        tmp_path,
        "notifications.yaml",
        """
notifications:
  channels: [unknown_channel, also_unknown]
  retry:
    max_attempts: 0
    base_delay_seconds: -1
    growth_factor: 0.5
  approval:
    timeout_seconds: 0
    threshold_amount: "-10"
    threshold_currency: ZZZ
""",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    errs = result.error.errors
    assert len(errs) > 1, (
        "Pydantic should collect multiple field errors in one pass; "
        f"got only {len(errs)}: {errs}"
    )
    locations = {e.location for e in errs}
    # Each invalid field appears as its own FieldValidationOutcome.
    assert "notifications.channels" in locations
    assert "notifications.retry.max_attempts" in locations
    assert "notifications.retry.base_delay_seconds" in locations
    assert "notifications.retry.growth_factor" in locations


def test_email_selector_without_settings_surfaces_cross_field_err(
    tmp_path: Path,
) -> None:
    """The `email` selector requires the `email:` sub-section; this
    is a cross-field invariant Pydantic per-field validators can't
    express. The model's `cross_field_errors()` surfaces it as a
    `<cross-field>` FieldValidationOutcome."""
    _write(
        tmp_path,
        "notifications.yaml",
        "notifications:\n  channels: [email]\n",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    errs = result.error.errors
    assert any(
        e.type == "invariant" and "email is missing" in e.msg
        for e in errs
    ), errs


def test_email_with_missing_recipients_surfaces_field_err(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "notifications.yaml",
        """
notifications:
  channels: [email]
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    user: alerts@example.com
    from_addr: alerts@example.com
    recipients: []
""",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    locations = {e.location for e in result.error.errors}
    assert "notifications.email.recipients" in locations


def test_extra_field_under_strict_schema(tmp_path: Path) -> None:
    """Pydantic's `extra='forbid'` catches typos that the existing
    loader silently ignores."""
    _write(
        tmp_path,
        "notifications.yaml",
        """
notifications:
  channels: [local_log]
  unknown_typo_field: "operator typed this by mistake"
""",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    locations = {e.location for e in result.error.errors}
    assert any("unknown_typo_field" in loc for loc in locations)


def test_smtp_port_out_of_range(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "notifications.yaml",
        """
notifications:
  channels: [email]
  email:
    smtp_host: smtp.example.com
    smtp_port: 70000
    user: alerts@example.com
    from_addr: alerts@example.com
    recipients: [operator@example.com]
""",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    locations = {e.location for e in result.error.errors}
    assert "notifications.email.smtp_port" in locations


def test_malformed_yaml(tmp_path: Path) -> None:
    _write(tmp_path, "notifications.yaml", "this:\n  - is\n    bad: yaml\n  syntax")
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    assert any(e.type == "config_parse" for e in result.error.errors)


def test_unreadable_config_dir_returns_err(tmp_path: Path) -> None:
    fake = tmp_path / "does_not_exist"
    result = validate_with_pydantic_schemas(fake)
    assert isinstance(result, Err)
    assert any(e.type == "config_io" for e in result.error.errors)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def test_render_rich_report_ok() -> None:
    report = RichValidationReport(
        validated_files=("notifications.yaml",), skipped_files=("risk.yaml",)
    )
    out = render_rich_report(report)
    assert "OK" in out
    assert "1 files validated" in out
    assert "skipped" in out


def test_render_rich_report_failed_groups_by_file() -> None:
    report = RichValidationReport(
        errors=(
            FieldValidationOutcome(
                file="notifications.yaml",
                location="notifications.retry.max_attempts",
                msg="Input should be greater than 0",
                type="greater_than",
            ),
            FieldValidationOutcome(
                file="notifications.yaml",
                location="notifications.channels",
                msg="not in {'email', 'local_log', 'slack'}",
                type="value_error",
            ),
        )
    )
    out = render_rich_report(report)
    assert "FAILED (2 field-level error(s))" in out
    assert "notifications.yaml:" in out
    assert "notifications.retry.max_attempts" in out
    assert "notifications.channels" in out


# ---------------------------------------------------------------------------
# Pydantic model — direct construction tests (sanity)
# ---------------------------------------------------------------------------


def test_notifications_yaml_default_construction() -> None:
    """Empty input ⇒ default model."""
    model = NotificationsYAML()
    assert model.notifications.channels == ["local_log"]
    assert model.notifications.retry.max_attempts == 3
    assert model.notifications.slack is None
    assert model.notifications.email is None
    assert model.cross_field_errors() == []


# ---------------------------------------------------------------------------
# risk.yaml — RiskYAML schema
# ---------------------------------------------------------------------------


def test_risk_yaml_default_construction() -> None:
    model = RiskYAML()
    assert model.risk.single_asset_cap == Decimal("0.30")
    assert model.risk.correlation_max == Decimal("0.85")
    assert model.risk.correlation_window_days == 60


def test_risk_yaml_bundled_config_validates(tmp_path: Path) -> None:
    """The repo's bundled risk.yaml SHALL validate cleanly."""
    import shutil

    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(repo_root / "config" / "risk.yaml", tmp_path / "risk.yaml")
    result = validate_with_pydantic_schemas(tmp_path)
    # risk.yaml validates; other schemas may surface "required file
    # missing" for risk/kill_switch — they're required per RICH_SCHEMAS.
    # Filter to risk.yaml's errors.
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    risk_errors = [e for e in errors if e.file == "risk.yaml"]
    assert risk_errors == [], risk_errors


def test_risk_yaml_collects_multiple_invariant_violations(tmp_path: Path) -> None:
    (tmp_path / "risk.yaml").write_text(
        """
risk:
  single_asset_cap: -0.5
  correlation_max: 1.5
  correlation_window_days: 0
  forbidden_regimes_for:
    unknown_class: [bull]
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    risk_errors = [e for e in result.error.errors if e.file == "risk.yaml"]
    locations = {e.location for e in risk_errors}
    assert "risk.single_asset_cap" in locations
    assert "risk.correlation_max" in locations
    assert "risk.correlation_window_days" in locations
    assert "risk.forbidden_regimes_for" in locations


def test_risk_yaml_extra_field_rejected(tmp_path: Path) -> None:
    (tmp_path / "risk.yaml").write_text(
        "risk:\n  single_asset_cap: 0.3\n  typo_field: oops\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    risk_errors = [e for e in result.error.errors if e.file == "risk.yaml"]
    assert any("typo_field" in e.location for e in risk_errors)


# ---------------------------------------------------------------------------
# kill_switch.yaml — KillSwitchYAML schema
# ---------------------------------------------------------------------------


def test_kill_switch_yaml_default_construction() -> None:
    model = KillSwitchYAML()
    assert model.kill_switch.financial.single_day_loss == Decimal("0.05")
    assert model.kill_switch.financial.rapid_decline.pct == Decimal("0.10")
    assert model.kill_switch.financial.rapid_decline.days == 5
    assert model.kill_switch.execution.rejection_threshold == Decimal("0.20")
    assert model.kill_switch.recovery.require_manual_token is True


def test_kill_switch_yaml_bundled_config_validates(tmp_path: Path) -> None:
    import shutil

    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(
        repo_root / "config" / "kill_switch.yaml", tmp_path / "kill_switch.yaml"
    )
    result = validate_with_pydantic_schemas(tmp_path)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    ks_errors = [e for e in errors if e.file == "kill_switch.yaml"]
    assert ks_errors == [], ks_errors


def test_kill_switch_yaml_collects_multiple_invariant_violations(tmp_path: Path) -> None:
    (tmp_path / "kill_switch.yaml").write_text(
        """
kill_switch:
  financial:
    single_day_loss: 0
    rapid_decline:
      pct: 1.5
      days: 0
  execution:
    rejection_threshold: 0
    slippage_anomaly_sigma: -1
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    ks_errors = [e for e in result.error.errors if e.file == "kill_switch.yaml"]
    locations = {e.location for e in ks_errors}
    assert "kill_switch.financial.single_day_loss" in locations
    assert "kill_switch.financial.rapid_decline.pct" in locations
    assert "kill_switch.financial.rapid_decline.days" in locations
    assert "kill_switch.execution.rejection_threshold" in locations
    assert "kill_switch.execution.slippage_anomaly_sigma" in locations


# ---------------------------------------------------------------------------
# mc_drawdown_floor.yaml — MCDrawdownFloorYAML schema
# ---------------------------------------------------------------------------


def test_mc_drawdown_floor_yaml_default_construction() -> None:
    model = MCDrawdownFloorYAML()
    assert model.mc_drawdown_floor.default == Decimal("0.15")
    assert model.mc_drawdown_floor.matrix == []


def test_mc_drawdown_floor_yaml_bundled_config_validates(tmp_path: Path) -> None:
    """The repo's bundled CR-031 mc_drawdown_floor.yaml SHALL validate."""
    import shutil

    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(
        repo_root / "config" / "mc_drawdown_floor.yaml",
        tmp_path / "mc_drawdown_floor.yaml",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    mc_errors = [e for e in errors if e.file == "mc_drawdown_floor.yaml"]
    assert mc_errors == [], mc_errors


def test_mc_drawdown_floor_rejects_unknown_phase_or_regime(tmp_path: Path) -> None:
    (tmp_path / "mc_drawdown_floor.yaml").write_text(
        """
mc_drawdown_floor:
  default: 0.20
  matrix:
    - phase: NINE
      regime: bull
      value: 0.15
    - phase: ONE
      regime: euphoric
      value: 0.12
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    mc_errors = [e for e in result.error.errors if e.file == "mc_drawdown_floor.yaml"]
    locations = {e.location for e in mc_errors}
    assert any("phase" in loc for loc in locations)
    assert any("regime" in loc for loc in locations)


def test_mc_drawdown_floor_rejects_negative_value(tmp_path: Path) -> None:
    (tmp_path / "mc_drawdown_floor.yaml").write_text(
        """
mc_drawdown_floor:
  default: -0.10
  matrix: []
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    mc_errors = [e for e in result.error.errors if e.file == "mc_drawdown_floor.yaml"]
    assert any(
        e.location == "mc_drawdown_floor.default" and "must be >= 0" in e.msg
        for e in mc_errors
    )


# ---------------------------------------------------------------------------
# Required-file enforcement
# ---------------------------------------------------------------------------


def test_required_files_missing_surfaces_field_err(tmp_path: Path) -> None:
    """risk + kill_switch + system + turbos + phases are required
    per RICH_SCHEMAS; absent files SHALL surface a
    FieldValidationOutcome with `type=config_io`."""
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    errors = result.error.errors
    files_with_io_errs = {
        e.file for e in errors if e.type == "config_io"
    }
    assert "risk.yaml" in files_with_io_errs
    assert "kill_switch.yaml" in files_with_io_errs
    assert "system.yaml" in files_with_io_errs
    assert "turbos.yaml" in files_with_io_errs
    assert "phases.yaml" in files_with_io_errs


# ---------------------------------------------------------------------------
# system.yaml — SystemYAML schema
# ---------------------------------------------------------------------------


def test_system_yaml_default_construction() -> None:
    model = SystemYAML()
    assert model.system.mode == "backtest"
    assert model.system.seed == 0
    assert model.broker.adapter == "local"
    assert model.data.provider == "mock"


def test_system_yaml_bundled_config_validates(tmp_path: Path) -> None:
    shutil.copy(_BUNDLED_CONFIG_DIR / "system.yaml", tmp_path / "system.yaml")
    result = validate_with_pydantic_schemas(tmp_path)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    sys_errors = [e for e in errors if e.file == "system.yaml"]
    assert sys_errors == [], sys_errors


def test_system_yaml_collects_invariant_violations(tmp_path: Path) -> None:
    (tmp_path / "system.yaml").write_text(
        """
system:
  starting_capital:
    amount: -100
    currency: ZZZ
  mode: invalid_mode
  seed: -1
broker:
  adapter: ""
data:
  provider: unknown_provider
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    sys_errors = [e for e in result.error.errors if e.file == "system.yaml"]
    locations = {e.location for e in sys_errors}
    assert "system.starting_capital.amount" in locations
    assert "system.starting_capital.currency" in locations
    assert "system.mode" in locations
    assert "data.provider" in locations


def test_system_yaml_accepts_hex_seed(tmp_path: Path) -> None:
    """The bundled YAML uses ``seed: 0xCAFE`` — YAML parses that as
    an int directly. The validator coerces string variants too."""
    (tmp_path / "system.yaml").write_text(
        """
system:
  starting_capital: {amount: 1000, currency: EUR}
  seed: 0xCAFE
  mode: backtest
broker:
  adapter: local
data:
  provider: mock
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    sys_errors = (
        [e for e in result.error.errors if e.file == "system.yaml"]
        if isinstance(result, Err)
        else []
    )
    assert sys_errors == [], sys_errors


# ---------------------------------------------------------------------------
# turbos.yaml — TurbosYAML schema
# ---------------------------------------------------------------------------


def test_turbos_yaml_default_construction() -> None:
    model = TurbosYAML()
    assert model.turbos.filter.knockout_min_distance == Decimal("0.05")
    assert model.turbos.scoring.threshold == Decimal("0.50")
    assert len(model.turbos.scoring.weights) == 4


def test_turbos_yaml_bundled_config_validates(tmp_path: Path) -> None:
    shutil.copy(_BUNDLED_CONFIG_DIR / "turbos.yaml", tmp_path / "turbos.yaml")
    result = validate_with_pydantic_schemas(tmp_path)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    turbo_errors = [e for e in errors if e.file == "turbos.yaml"]
    assert turbo_errors == [], turbo_errors


def test_turbos_yaml_rejects_weights_that_dont_sum_to_one(tmp_path: Path) -> None:
    (tmp_path / "turbos.yaml").write_text(
        """
turbos:
  filter:
    knockout_min_distance: 0.05
    spread_max: 0.015
    min_liquidity: 100000
    max_volatility: 0.50
  scoring:
    weights: [0.5, 0.5, 0.5, 0.5]
    threshold: 0.50
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    turbo_errors = [e for e in result.error.errors if e.file == "turbos.yaml"]
    assert any(
        e.location == "turbos.scoring.weights" and "sum" in e.msg
        for e in turbo_errors
    )


def test_turbos_yaml_rejects_wrong_weight_count(tmp_path: Path) -> None:
    (tmp_path / "turbos.yaml").write_text(
        """
turbos:
  scoring:
    weights: [0.5, 0.5]
    threshold: 0.5
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Err)
    turbo_errors = [e for e in result.error.errors if e.file == "turbos.yaml"]
    assert any("4 entries" in e.msg for e in turbo_errors)


# ---------------------------------------------------------------------------
# webui.yaml — WebUIYAML schema
# ---------------------------------------------------------------------------


def test_webui_yaml_default_construction() -> None:
    model = WebUIYAML()
    assert model.webui.host == "127.0.0.1"
    assert model.webui.port == 8080
    assert model.webui.idempotency_backend == "memory"


def test_webui_yaml_bundled_config_validates(seeded_config_dir: Path) -> None:
    shutil.copy(_BUNDLED_CONFIG_DIR / "webui.yaml", seeded_config_dir / "webui.yaml")
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    webui_errors = [e for e in errors if e.file == "webui.yaml"]
    assert webui_errors == [], webui_errors


def test_webui_yaml_rejects_unknown_backend(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "webui.yaml").write_text(
        """
webui:
  idempotency_backend: not_a_backend
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    webui_errors = [e for e in result.error.errors if e.file == "webui.yaml"]
    assert any(
        e.location == "webui.idempotency_backend" for e in webui_errors
    )


def test_webui_yaml_rejects_port_out_of_range(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "webui.yaml").write_text(
        "webui:\n  port: 99999\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    webui_errors = [e for e in result.error.errors if e.file == "webui.yaml"]
    assert any(e.location == "webui.port" for e in webui_errors)


# ---------------------------------------------------------------------------
# logging.yaml — LoggingYAML schema
# ---------------------------------------------------------------------------


def test_logging_yaml_default_construction() -> None:
    model = LoggingYAML()
    assert model.logging.level == "INFO"
    assert model.logging.format == "json"
    assert model.logging.file_path is None


def test_logging_yaml_bundled_config_validates(seeded_config_dir: Path) -> None:
    """The repo's bundled logging.yaml validates cleanly."""
    bundled = _BUNDLED_CONFIG_DIR / "logging.yaml"
    if bundled.exists():
        shutil.copy(bundled, seeded_config_dir / "logging.yaml")
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    log_errors = [e for e in errors if e.file == "logging.yaml"]
    assert log_errors == [], log_errors


def test_logging_yaml_rejects_unknown_level(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "logging.yaml").write_text(
        "logging:\n  level: NOISY\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    log_errors = [e for e in result.error.errors if e.file == "logging.yaml"]
    assert any(e.location == "logging.level" for e in log_errors)


def test_logging_yaml_rejects_unknown_format(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "logging.yaml").write_text(
        "logging:\n  format: yaml\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    log_errors = [e for e in result.error.errors if e.file == "logging.yaml"]
    assert any(e.location == "logging.format" for e in log_errors)


def test_logging_yaml_accepts_null_file_path(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "logging.yaml").write_text(
        "logging:\n  level: INFO\n  format: json\n  file_path: null\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    log_errors = [e for e in errors if e.file == "logging.yaml"]
    assert log_errors == [], log_errors


def test_logging_yaml_rejects_empty_file_path(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "logging.yaml").write_text(
        "logging:\n  level: INFO\n  format: json\n  file_path: \"\"\n",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    log_errors = [e for e in result.error.errors if e.file == "logging.yaml"]
    assert any(e.location == "logging.file_path" for e in log_errors)


# ---------------------------------------------------------------------------
# phases.yaml — PhasesYAML schema
# ---------------------------------------------------------------------------


def _valid_phase_row(**overrides) -> dict:
    """A minimally-valid per-phase constraint dict; overlay overrides."""
    base = {
        "max_positions": 3,
        "max_trades_per_month": 4,
        "allocation_targets": {
            "stock": "0.90",
            "tactical": "0.10",
            "structured": "0.0",
            "turbo": "0.0",
            "cash": "0.0",
        },
        "turbo_exposure_max": "0.0",
        "risk_per_trade_band": ["0.01", "0.02"],
        "max_drawdown": "0.15",
    }
    base.update(overrides)
    return base


def _valid_phases_yaml(phase_overrides: dict[int, dict] | None = None) -> str:
    """Render a full phases.yaml string for tests. ``phase_overrides``
    maps phase numbers (1..6) to per-phase override dicts."""
    import yaml as _yaml

    overrides = phase_overrides or {}
    constraints = {n: _valid_phase_row() for n in range(1, 7)}
    for n, row_overrides in overrides.items():
        constraints[n].update(row_overrides)
    payload = {
        "phases": {
            "bounds": [3000, 10000, 50000, 200000, 1000000],
            "hysteresis": "0.10",
            "constraints": constraints,
        }
    }
    return _yaml.safe_dump(payload, sort_keys=False)


def test_phases_yaml_bundled_config_validates(tmp_path: Path) -> None:
    """The repo's bundled phases.yaml validates cleanly."""
    shutil.copy(_BUNDLED_CONFIG_DIR / "phases.yaml", tmp_path / "phases.yaml")
    result = validate_with_pydantic_schemas(tmp_path)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    phase_errors = [e for e in errors if e.file == "phases.yaml"]
    assert phase_errors == [], phase_errors


def _phases_yaml_with_bounds(bounds: list) -> str:
    """Render a phases.yaml string with an arbitrary bounds list."""
    import yaml as _yaml

    payload = {
        "phases": {
            "bounds": bounds,
            "hysteresis": "0.10",
            "constraints": {n: _valid_phase_row() for n in range(1, 7)},
        }
    }
    return _yaml.safe_dump(payload, sort_keys=False)


def test_phases_yaml_rejects_wrong_bounds_count(seeded_config_dir: Path) -> None:
    """bounds must list exactly 5 thresholds (6 phases)."""
    (seeded_config_dir / "phases.yaml").write_text(
        _phases_yaml_with_bounds([3000, 10000, 50000]),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        e.location == "phases.bounds" and "5" in e.msg for e in phase_errors
    )


def test_phases_yaml_rejects_non_monotonic_bounds(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "phases.yaml").write_text(
        _phases_yaml_with_bounds([10000, 3000, 50000, 200000, 1000000]),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        "increasing" in e.msg for e in phase_errors
    )


def test_phases_yaml_rejects_missing_phase_row(seeded_config_dir: Path) -> None:
    """All six phase rows must be present."""
    import yaml as _yaml

    payload = {
        "phases": {
            "bounds": [3000, 10000, 50000, 200000, 1000000],
            "hysteresis": "0.10",
            "constraints": {
                n: _valid_phase_row() for n in range(1, 6)
            },  # missing phase 6
        }
    }
    (seeded_config_dir / "phases.yaml").write_text(
        _yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any("missing phase" in e.msg for e in phase_errors)


def test_phases_yaml_rejects_allocation_targets_not_summing_to_one(
    seeded_config_dir: Path,
) -> None:
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml(
            {
                1: {
                    "allocation_targets": {
                        "stock": "0.50",
                        "tactical": "0.20",
                        "structured": "0.0",
                        "turbo": "0.0",
                        "cash": "0.0",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        "allocation_targets" in e.location and "sum" in e.msg
        for e in phase_errors
    )


def test_phases_yaml_rejects_unknown_allocation_bucket(
    seeded_config_dir: Path,
) -> None:
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml(
            {
                1: {
                    "allocation_targets": {
                        "stock": "0.50",
                        "crypto": "0.50",  # unknown bucket
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        "crypto" in e.msg or "allocation_targets" in e.location
        for e in phase_errors
    )


def test_phases_yaml_rejects_invalid_risk_band(seeded_config_dir: Path) -> None:
    """risk_per_trade_band must be [lo, hi] with lo < hi."""
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml({1: {"risk_per_trade_band": ["0.05", "0.01"]}}),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        "risk_per_trade_band" in e.location for e in phase_errors
    )


def test_phases_yaml_rejects_invalid_max_drawdown(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml({1: {"max_drawdown": "1.5"}}),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        e.location.endswith("max_drawdown") for e in phase_errors
    )


def test_phases_yaml_accepts_portfolio_vol_cap(seeded_config_dir: Path) -> None:
    """Phase 5+ rows set `portfolio_vol_cap`; null is also valid."""
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml(
            {
                5: {"portfolio_vol_cap": "0.12"},
                6: {"portfolio_vol_cap": None},
            }
        ),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    phase_errors = [e for e in errors if e.file == "phases.yaml"]
    assert phase_errors == [], phase_errors


def test_phases_yaml_rejects_out_of_range_portfolio_vol_cap(
    seeded_config_dir: Path,
) -> None:
    (seeded_config_dir / "phases.yaml").write_text(
        _valid_phases_yaml({5: {"portfolio_vol_cap": "1.5"}}),
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    phase_errors = [e for e in result.error.errors if e.file == "phases.yaml"]
    assert any(
        e.location.endswith("portfolio_vol_cap") for e in phase_errors
    )


# ---------------------------------------------------------------------------
# quant.yaml — QuantYAML schema
# ---------------------------------------------------------------------------


def test_quant_yaml_default_construction() -> None:
    model = QuantYAML()
    assert model.quant.validator.min_duration_days_for_1d == 30
    assert model.quant.overfitting.ratio_max == Decimal("0.10")
    assert model.quant.overfitting.ic_floor == Decimal("0.30")


def test_quant_yaml_bundled_config_validates(seeded_config_dir: Path) -> None:
    bundled = _BUNDLED_CONFIG_DIR / "quant.yaml"
    if bundled.exists():
        shutil.copy(bundled, seeded_config_dir / "quant.yaml")
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    quant_errors = [e for e in errors if e.file == "quant.yaml"]
    assert quant_errors == [], quant_errors


def test_quant_yaml_rejects_ratio_max_out_of_range(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "quant.yaml").write_text(
        """
quant:
  overfitting:
    ratio_max: "1.5"
    ic_floor: "0.30"
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    quant_errors = [e for e in result.error.errors if e.file == "quant.yaml"]
    assert any(
        e.location == "quant.overfitting.ratio_max" for e in quant_errors
    )


def test_quant_yaml_rejects_ic_floor_out_of_range(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "quant.yaml").write_text(
        """
quant:
  overfitting:
    ratio_max: "0.10"
    ic_floor: "2.0"
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    quant_errors = [e for e in result.error.errors if e.file == "quant.yaml"]
    assert any(
        e.location == "quant.overfitting.ic_floor" for e in quant_errors
    )


def test_quant_yaml_rejects_bounds_with_hi_below_lo(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "quant.yaml").write_text(
        """
quant:
  validator:
    bounds_table:
      sharpe:
        lo: 5
        hi: -3
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    quant_errors = [e for e in result.error.errors if e.file == "quant.yaml"]
    assert any(
        "lo" in e.msg or "bounds_table" in e.location for e in quant_errors
    )


def test_quant_yaml_rejects_invalid_min_duration(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "quant.yaml").write_text(
        """
quant:
  validator:
    min_duration_days_for_1d: 0
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    quant_errors = [e for e in result.error.errors if e.file == "quant.yaml"]
    assert any(
        e.location == "quant.validator.min_duration_days_for_1d"
        for e in quant_errors
    )


# ---------------------------------------------------------------------------
# accounts.yaml — AccountsYAML schema
# ---------------------------------------------------------------------------


def test_accounts_yaml_default_construction() -> None:
    """Empty model ⇒ no accounts (caller falls back to default registry)."""
    model = AccountsYAML()
    assert model.accounts == []


def test_accounts_yaml_full_valid_list(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "accounts.yaml").write_text(
        """
accounts:
  - id: alpha
    tax_model: france_cto
    operator_token_account_id: alpha
  - id: beta
    tax_model: france_cto
    operator_token_account_id: beta
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    errors = result.error.errors if isinstance(result, Err) else result.value.errors
    acc_errors = [e for e in errors if e.file == "accounts.yaml"]
    assert acc_errors == [], acc_errors


def test_accounts_yaml_rejects_duplicate_ids(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "accounts.yaml").write_text(
        """
accounts:
  - id: alpha
    tax_model: france_cto
    operator_token_account_id: alpha
  - id: alpha
    tax_model: france_cto
    operator_token_account_id: alpha2
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    acc_errors = [e for e in result.error.errors if e.file == "accounts.yaml"]
    assert any("duplicate" in e.msg for e in acc_errors)


def test_accounts_yaml_rejects_unknown_tax_model(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "accounts.yaml").write_text(
        """
accounts:
  - id: alpha
    tax_model: belgium_pfu
    operator_token_account_id: alpha
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    acc_errors = [e for e in result.error.errors if e.file == "accounts.yaml"]
    assert any(
        e.location.endswith("tax_model") for e in acc_errors
    )


def test_accounts_yaml_rejects_empty_id(seeded_config_dir: Path) -> None:
    (seeded_config_dir / "accounts.yaml").write_text(
        """
accounts:
  - id: ""
    tax_model: france_cto
    operator_token_account_id: x
""",
        encoding="utf-8",
    )
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Err)
    acc_errors = [e for e in result.error.errors if e.file == "accounts.yaml"]
    assert any(e.location.endswith(".id") for e in acc_errors)


def test_accounts_yaml_absent_skipped(seeded_config_dir: Path) -> None:
    """accounts.yaml is optional — absent ⇒ skipped, no error."""
    # Don't write accounts.yaml.
    result = validate_with_pydantic_schemas(seeded_config_dir)
    assert isinstance(result, Ok), result
    assert "accounts.yaml" in result.value.skipped_files
