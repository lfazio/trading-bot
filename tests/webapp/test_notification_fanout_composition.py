"""CR-001 Phase B — webapp notification fan-out composition.

The webapp's ``default_app()`` constructs a ``NotificationFanOut``
that subscribes:

- Every channel declared in ``config/notifications.yaml`` (the
  YAML-driven set: local_log + optional slack + optional email).
- The runtime-owned ``InboxChannel`` (passed as ``extra=...``)
  so dashboard alerts always land in the operator's inbox AND
  on their configured external channels simultaneously.

These tests exercise the ``build_notification_fanout`` helper
in isolation — without bringing up the full webapp — so the
composition is byte-deterministic and the fallback behaviour
on missing / invalid YAMLs is pinned.
"""

from __future__ import annotations

from pathlib import Path

from trading_system.notifications.fanout import NotificationFanOut
from trading_system.webapp.app import build_notification_fanout
from trading_system.webapp.inbox import InboxChannel


def test_build_fanout_with_bundled_config_only_local_log_and_inbox(
    tmp_path: Path,
) -> None:
    """Empty config_dir ⇒ defaults from `NotificationsConfig()`
    (local_log only) + the inbox via the `extra` channel."""
    inbox = InboxChannel()
    # tmp_path is empty: no notifications.yaml exists, so the
    # helper falls back to defaults.
    fanout = build_notification_fanout(inbox=inbox, config_dir=tmp_path)
    assert isinstance(fanout, NotificationFanOut)
    # Default channels: local_log + inbox (via extra).
    channel_types = {type(c).__name__ for c in fanout.channels}
    assert "LocalLogChannel" in channel_types
    assert "InboxChannel" in channel_types


def test_build_fanout_includes_slack_when_yaml_opts_in(tmp_path: Path) -> None:
    """`channels: [local_log, slack]` in the YAML ⇒ SlackNotificationChannel
    appears in the fan-out alongside local_log + inbox."""
    yaml_path = tmp_path / "notifications.yaml"
    yaml_path.write_text(
        """
notifications:
  channels:
    - local_log
    - slack
  slack:
    webhook_url_env: CUSTOM_WEBHOOK_ENV
""",
        encoding="utf-8",
    )
    inbox = InboxChannel()
    fanout = build_notification_fanout(inbox=inbox, config_dir=tmp_path)
    channel_types = {type(c).__name__ for c in fanout.channels}
    assert "LocalLogChannel" in channel_types
    assert "SlackNotificationChannel" in channel_types
    assert "InboxChannel" in channel_types


def test_build_fanout_invalid_yaml_falls_back_to_defaults(tmp_path: Path) -> None:
    """A present-but-broken YAML ⇒ logs the issue + falls back to
    defaults so the webapp keeps booting. The inbox is still
    subscribed."""
    yaml_path = tmp_path / "notifications.yaml"
    yaml_path.write_text(
        # Email selector without the required email sub-section
        # — `NotificationsConfig.__post_init__` raises ValueError.
        "notifications:\n  channels: [email]\n",
        encoding="utf-8",
    )
    inbox = InboxChannel()
    fanout = build_notification_fanout(inbox=inbox, config_dir=tmp_path)
    # Fell back to defaults — no EmailNotificationChannel.
    channel_types = {type(c).__name__ for c in fanout.channels}
    assert "EmailNotificationChannel" not in channel_types
    assert "LocalLogChannel" in channel_types
    assert "InboxChannel" in channel_types


def test_build_fanout_retry_policy_from_yaml(tmp_path: Path) -> None:
    """The fan-out's `retry_policy` mirrors the YAML's `retry`
    sub-section — operators tune attempt counts + backoff
    without restarting any other surface."""
    yaml_path = tmp_path / "notifications.yaml"
    yaml_path.write_text(
        """
notifications:
  channels: [local_log]
  retry:
    max_attempts: 7
    base_delay_seconds: 0.5
    growth_factor: 3.0
""",
        encoding="utf-8",
    )
    fanout = build_notification_fanout(
        inbox=InboxChannel(), config_dir=tmp_path
    )
    assert fanout.retry_policy.max_attempts == 7
    assert fanout.retry_policy.base_delay_seconds == 0.5
    assert fanout.retry_policy.growth_factor == 3.0


def test_build_fanout_dispatch_delivers_to_inbox(tmp_path: Path) -> None:
    """End-to-end: dispatching a payload through the constructed
    fan-out lands an entry in the inbox channel — confirming the
    inbox subscription via `extra=` is wired correctly."""
    from datetime import UTC, datetime

    from trading_system.models.identifiers import AccountId
    from trading_system.notifications.payloads import AnomalyAlert

    inbox = InboxChannel()
    fanout = build_notification_fanout(inbox=inbox, config_dir=tmp_path)
    payload = AnomalyAlert(
        at=datetime(2026, 5, 31, tzinfo=UTC),
        code="ks_tripped",
        severity="WARN",
        account_id=AccountId("alpha"),
        message="test fan-out delivery",
    )
    fanout.dispatch(payload)
    entries = inbox.snapshot()
    assert len(entries) == 1
    assert entries[0].message == "test fan-out delivery"
    assert entries[0].code == "ks_tripped"
    assert entries[0].category == "anomaly"
    assert entries[0].severity == "warn"
    assert entries[0].account_id == "alpha"
