"""Tests for ``trading_system.notifications.loader`` (REQ_SDD_NOT_008)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.notifications.loader import (
    ApprovalConfig,
    NotificationsConfig,
    RetryConfig,
    load_notifications_config,
)
from trading_system.result import Err, Ok


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "notifications.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# RetryConfig invariants
# ---------------------------------------------------------------------------


def test_retry_defaults() -> None:
    r = RetryConfig()
    assert r.max_attempts == 3
    assert r.base_delay_seconds == 0.05
    assert r.growth_factor == 2.0


def test_retry_rejects_zero_max_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryConfig(max_attempts=0)


def test_retry_rejects_negative_base_delay() -> None:
    with pytest.raises(ValueError, match="base_delay_seconds"):
        RetryConfig(base_delay_seconds=-1.0)


def test_retry_rejects_growth_factor_below_1() -> None:
    with pytest.raises(ValueError, match="growth_factor"):
        RetryConfig(growth_factor=0.5)


# ---------------------------------------------------------------------------
# ApprovalConfig invariants
# ---------------------------------------------------------------------------


def test_approval_defaults() -> None:
    a = ApprovalConfig()
    assert a.timeout_seconds == 60
    assert a.threshold_amount == Decimal("0")
    assert a.threshold_currency == "EUR"


def test_approval_rejects_zero_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        ApprovalConfig(timeout_seconds=0)


def test_approval_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError, match="threshold_amount"):
        ApprovalConfig(threshold_amount=Decimal("-1"))


def test_approval_rejects_unknown_currency() -> None:
    with pytest.raises(ValueError, match="threshold_currency"):
        ApprovalConfig(threshold_currency="XYZ")


# ---------------------------------------------------------------------------
# NotificationsConfig invariants
# ---------------------------------------------------------------------------


def test_notifications_defaults() -> None:
    n = NotificationsConfig()
    assert n.channels == ("local_log",)
    assert n.retry == RetryConfig()
    assert n.approval == ApprovalConfig()


def test_notifications_rejects_empty_channels() -> None:
    with pytest.raises(ValueError, match="channels"):
        NotificationsConfig(channels=())


def test_notifications_rejects_unknown_channel() -> None:
    with pytest.raises(ValueError, match="channels"):
        NotificationsConfig(channels=("unknown",))


def test_notifications_rejects_duplicate_channel() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        NotificationsConfig(channels=("local_log", "local_log"))


def test_notifications_rejects_empty_local_log_path() -> None:
    with pytest.raises(ValueError, match="local_log_path"):
        NotificationsConfig(local_log_path="   ")


# ---------------------------------------------------------------------------
# Loader happy path
# ---------------------------------------------------------------------------


def test_loads_explicit_fields(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
notifications:
  channels:
    - local_log
  retry:
    max_attempts: 5
    base_delay_seconds: 0.1
    growth_factor: 1.5
  approval:
    timeout_seconds: 30
    threshold_amount: "10000"
    threshold_currency: EUR
  local_log_path: /tmp/notifications.jsonl
""",
    )
    cfg = load_notifications_config(p).unwrap()
    assert cfg.channels == ("local_log",)
    assert cfg.retry.max_attempts == 5
    assert cfg.retry.base_delay_seconds == 0.1
    assert cfg.retry.growth_factor == 1.5
    assert cfg.approval.timeout_seconds == 30
    assert cfg.approval.threshold_amount == Decimal("10000")
    assert cfg.local_log_path == "/tmp/notifications.jsonl"


def test_empty_file_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    assert load_notifications_config(p).unwrap() == NotificationsConfig()


def test_absent_section_returns_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "other: 1\n")
    assert load_notifications_config(p).unwrap() == NotificationsConfig()


def test_partial_section_returns_partial_overrides(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
notifications:
  retry:
    max_attempts: 7
""",
    )
    cfg = load_notifications_config(p).unwrap()
    # Overridden field landed; siblings keep their defaults.
    assert cfg.retry.max_attempts == 7
    assert cfg.retry.base_delay_seconds == 0.05
    assert cfg.channels == ("local_log",)
    assert cfg.approval == ApprovalConfig()


# ---------------------------------------------------------------------------
# Loader error categories
# ---------------------------------------------------------------------------


def test_missing_file_returns_io_err(tmp_path: Path) -> None:
    match load_notifications_config(tmp_path / "ghost.yaml"):
        case Err(reason):
            assert reason.startswith("config:io:")
        case _:
            raise AssertionError("expected Err")


def test_malformed_yaml_returns_parse_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "notifications: {a: b\n")
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:parse:")
        case _:
            raise AssertionError("expected Err")


def test_non_mapping_top_level_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "- one\n")
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_non_list_channels_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "notifications:\n  channels: local_log\n")
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "channels" in reason
        case _:
            raise AssertionError("expected Err")


def test_unknown_channel_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "notifications:\n  channels: ['voodoo']\n",
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:") and "channels" in reason
        case _:
            raise AssertionError("expected Err")


def test_bad_threshold_currency_returns_invariant_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "notifications:\n  approval:\n    threshold_currency: XYZ\n",
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:invariant:") and "currency" in reason
        case _:
            raise AssertionError("expected Err")


def test_non_int_max_attempts_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path, "notifications:\n  retry:\n    max_attempts: 'three'\n"
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")


def test_bad_threshold_amount_returns_schema_err(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "notifications:\n  approval:\n    threshold_amount: not-a-number\n",
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:") and "threshold_amount" in reason
        case _:
            raise AssertionError("expected Err")


def test_non_string_local_log_path_returns_schema_err(tmp_path: Path) -> None:
    p = _write(tmp_path, "notifications:\n  local_log_path: 5\n")
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
        case _:
            raise AssertionError("expected Err")
