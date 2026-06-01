"""C3 — Pydantic v2 schema validation tests.

REQ refs: REQ_SDS_CFG_001 (validated at startup),
REQ_SDS_CFG_002 (absent file ⇒ defaults), REQ_SDD_ERR_002
(categorised Errs preserved through the rich path).
"""

from __future__ import annotations

from pathlib import Path

from trading_system.config.pydantic_schemas import (
    FieldValidationOutcome,
    NotificationsYAML,
    RichValidationReport,
    render_rich_report,
    validate_with_pydantic_schemas,
)
from trading_system.result import Err, Ok


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_absent_notifications_yaml_skipped(tmp_path: Path) -> None:
    """No file ⇒ skipped (optional). Report stays Ok."""
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Ok)
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.skipped_files


def test_empty_notifications_yaml_validates(tmp_path: Path) -> None:
    """Empty file ⇒ Pydantic uses defaults (the documented
    absent-section-defaults behaviour)."""
    _write(tmp_path, "notifications.yaml", "")
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Ok)
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.validated_files


def test_full_valid_notifications_yaml(tmp_path: Path) -> None:
    _write(
        tmp_path,
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
    result = validate_with_pydantic_schemas(tmp_path)
    assert isinstance(result, Ok), result
    assert result.value.errors == ()
    assert "notifications.yaml" in result.value.validated_files


def test_email_channel_with_full_settings(tmp_path: Path) -> None:
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
    recipients:
      - operator@example.com
""",
    )
    result = validate_with_pydantic_schemas(tmp_path)
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
