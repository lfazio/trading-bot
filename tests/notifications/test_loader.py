"""Tests for ``trading_system.notifications.loader`` (REQ_SDD_NOT_008)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.notifications.loader import (
    ApprovalConfig,
    EmailChannelConfig,
    NotificationsConfig,
    RetryConfig,
    SlackChannelConfig,
    build_channels,
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


# ---------------------------------------------------------------------------
# CR-001 Phase B — slack + email channel selectors
# ---------------------------------------------------------------------------


def test_slack_channel_config_defaults() -> None:
    cfg = SlackChannelConfig()
    assert cfg.webhook_url_env == "TRADING_BOT_SLACK_WEBHOOK_URL"
    assert cfg.timeout_seconds == 5.0


def test_slack_channel_config_rejects_empty_env_name() -> None:
    with pytest.raises(ValueError, match="webhook_url_env"):
        SlackChannelConfig(webhook_url_env="")


def test_slack_channel_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        SlackChannelConfig(timeout_seconds=0)


def test_email_channel_config_invariants() -> None:
    """Every required field carries an invariant — missing
    smtp_host / smtp_port out of range / empty recipients all
    raise ValueError."""
    base = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "user": "alerts@example.com",
        "from_addr": "alerts@example.com",
        "recipients": ("operator@example.com",),
    }
    # Happy path constructs cleanly.
    cfg = EmailChannelConfig(**base)  # type: ignore[arg-type]
    assert cfg.use_starttls is True

    with pytest.raises(ValueError, match="smtp_host"):
        EmailChannelConfig(**{**base, "smtp_host": ""})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="smtp_port"):
        EmailChannelConfig(**{**base, "smtp_port": 0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="recipients"):
        EmailChannelConfig(**{**base, "recipients": ()})  # type: ignore[arg-type]


def test_notifications_config_accepts_slack_channel() -> None:
    cfg = NotificationsConfig(
        channels=("local_log", "slack"),
        slack=SlackChannelConfig(),
    )
    assert "slack" in cfg.channels


def test_notifications_config_rejects_email_without_settings() -> None:
    """email selector without an email sub-section is a hard
    schema error — the channel needs SMTP settings."""
    with pytest.raises(ValueError, match="notifications.email is missing"):
        NotificationsConfig(channels=("local_log", "email"))


def test_notifications_config_accepts_email_with_settings() -> None:
    cfg = NotificationsConfig(
        channels=("email",),
        email=EmailChannelConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            user="alerts@example.com",
            from_addr="alerts@example.com",
            recipients=("operator@example.com",),
        ),
    )
    assert "email" in cfg.channels
    assert cfg.email is not None
    assert cfg.email.smtp_host == "smtp.example.com"


def test_loader_parses_slack_subsection(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
notifications:
  channels: [local_log, slack]
  slack:
    webhook_url_env: CUSTOM_WEBHOOK_ENV
    timeout_seconds: 3
""",
    )
    match load_notifications_config(p):
        case Ok(cfg):
            assert cfg.channels == ("local_log", "slack")
            assert cfg.slack is not None
            assert cfg.slack.webhook_url_env == "CUSTOM_WEBHOOK_ENV"
            assert cfg.slack.timeout_seconds == 3.0
        case _:
            raise AssertionError("expected Ok")


def test_loader_parses_email_subsection(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
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
      - oncall@example.com
    use_starttls: true
""",
    )
    match load_notifications_config(p):
        case Ok(cfg):
            assert cfg.channels == ("email",)
            assert cfg.email is not None
            assert cfg.email.smtp_host == "smtp.example.com"
            assert cfg.email.recipients == (
                "operator@example.com",
                "oncall@example.com",
            )
        case _:
            raise AssertionError("expected Ok")


def test_loader_rejects_email_selector_without_settings(tmp_path: Path) -> None:
    p = _write(tmp_path, "notifications:\n  channels: [email]\n")
    match load_notifications_config(p):
        case Err(reason):
            assert "config:invariant" in reason
            assert "email is missing" in reason
        case _:
            raise AssertionError("expected Err")


def test_loader_rejects_non_string_smtp_host(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
notifications:
  channels: [email]
  email:
    smtp_host: 12345
    smtp_port: 587
    user: alerts@example.com
    from_addr: alerts@example.com
    recipients: [operator@example.com]
""",
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
            assert "smtp_host" in reason
        case _:
            raise AssertionError("expected Err")


def test_loader_rejects_email_recipients_not_list(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        """
notifications:
  channels: [email]
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    user: alerts@example.com
    from_addr: alerts@example.com
    recipients: not_a_list
""",
    )
    match load_notifications_config(p):
        case Err(reason):
            assert reason.startswith("config:schema:")
            assert "recipients" in reason
        case _:
            raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# build_channels factory
# ---------------------------------------------------------------------------


def test_build_channels_local_log_only(tmp_path: Path) -> None:
    cfg = NotificationsConfig(
        channels=("local_log",),
        local_log_path=str(tmp_path / "notifications.jsonl"),
    )
    channels = build_channels(cfg)
    assert len(channels) == 1
    # Channel type — read its class name without coupling to the
    # exact import path.
    assert type(channels[0]).__name__ == "LocalLogChannel"


def test_build_channels_slack_carries_config() -> None:
    cfg = NotificationsConfig(
        channels=("slack",),
        slack=SlackChannelConfig(
            webhook_url_env="CUSTOM_ENV", timeout_seconds=7.5
        ),
    )
    channels = build_channels(cfg)
    assert len(channels) == 1
    assert type(channels[0]).__name__ == "SlackNotificationChannel"
    assert channels[0].webhook_url_env == "CUSTOM_ENV"  # type: ignore[attr-defined]
    assert channels[0].timeout_seconds == 7.5  # type: ignore[attr-defined]


def test_build_channels_slack_default_config_when_absent() -> None:
    """No slack: sub-section ⇒ build_channels uses the channel's
    default env-var name."""
    cfg = NotificationsConfig(
        channels=("slack",),
    )
    channels = build_channels(cfg)
    assert channels[0].webhook_url_env == "TRADING_BOT_SLACK_WEBHOOK_URL"  # type: ignore[attr-defined]


def test_build_channels_email_uses_full_smtp_settings() -> None:
    cfg = NotificationsConfig(
        channels=("email",),
        email=EmailChannelConfig(
            smtp_host="smtp.example.com",
            smtp_port=465,
            user="alerts@example.com",
            from_addr="alerts@example.com",
            recipients=("operator@example.com",),
            use_starttls=False,
        ),
    )
    channels = build_channels(cfg)
    assert len(channels) == 1
    email = channels[0]
    assert type(email).__name__ == "EmailNotificationChannel"
    assert email.smtp_port == 465  # type: ignore[attr-defined]
    assert email.use_starttls is False  # type: ignore[attr-defined]


def test_build_channels_preserves_yaml_selector_order() -> None:
    """Channel order matches the YAML selector list — precondition
    for replay determinism on the fan-out subscriber order."""
    cfg = NotificationsConfig(
        channels=("slack", "local_log"),
        slack=SlackChannelConfig(),
    )
    channels = build_channels(cfg)
    assert type(channels[0]).__name__ == "SlackNotificationChannel"
    assert type(channels[1]).__name__ == "LocalLogChannel"


def test_build_channels_appends_extra_after_yaml_configured() -> None:
    """``extra`` channels land after the YAML-configured ones —
    documented for callers (e.g., the webapp's InboxChannel)."""

    class _StubExtraChannel:
        def deliver(self, payload):  # type: ignore[no-untyped-def]
            del payload
            return None

    extra = _StubExtraChannel()
    cfg = NotificationsConfig(channels=("local_log",))
    channels = build_channels(cfg, extra=(extra,))
    assert len(channels) == 2
    assert type(channels[0]).__name__ == "LocalLogChannel"
    assert channels[1] is extra
