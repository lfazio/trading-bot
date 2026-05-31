"""Loader for ``config/notifications.yaml`` — REQ_SDD_NOT_008.

Frozen ``NotificationsConfig`` covering the operator-tunable knobs
the Phase A notification surface needs:

- ``channels`` — closed v1 set ``{"local_log", "slack", "email"}``;
  ``local_log`` is the always-available baseline. ``slack`` reads
  webhook URL from an env var; ``email`` carries SMTP settings.
- ``retry`` — ``max_attempts`` / ``base_delay_seconds`` /
  ``growth_factor`` mirroring ``RetryPolicy``.
- ``approval`` — ``timeout_seconds`` / ``threshold_amount`` /
  ``threshold_currency``.
- ``local_log_path`` — file path the ``LocalLogChannel`` writes to.
- ``slack`` — ``webhook_url_env`` + ``timeout_seconds``; optional
  (the channel's defaults work for the standard env-var setup).
- ``email`` — SMTP settings (``smtp_host`` / ``smtp_port`` /
  ``user`` / ``from_addr`` / ``recipients`` + optional knobs).
  REQUIRED when ``"email"`` appears in the channels list.

Absent file ⇒ ``Ok(NotificationsConfig())`` so a deployment without
``config/notifications.yaml`` keeps using built-in defaults
(REQ_SDS_CFG_002). Present file fails the C2 startup gate on a bad
shape.

The loader is the schema gate AND the channel-factory: callers
invoke ``build_channels(config)`` to get the concrete channel
instances, then construct a ``NotificationFanOut`` around them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from trading_system.models.money import Currency
from trading_system.notifications.channel import NotificationChannel
from trading_system.notifications.channels.email import EmailNotificationChannel
from trading_system.notifications.channels.local_log import LocalLogChannel
from trading_system.notifications.channels.slack import (
    DEFAULT_WEBHOOK_URL_ENV,
    SlackNotificationChannel,
)
from trading_system.result import Err, Ok, Result, catch


# Closed set of channel selectors recognised by v1.
_CHANNEL_SELECTORS: frozenset[str] = frozenset(
    {"local_log", "slack", "email"}
)

_VALID_CURRENCIES: frozenset[str] = frozenset(c.value for c in Currency)


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Mirrors ``notifications.fanout.RetryPolicy``."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    growth_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError(
                f"RetryConfig.max_attempts must be > 0, got {self.max_attempts}"
            )
        if self.base_delay_seconds < 0:
            raise ValueError(
                f"RetryConfig.base_delay_seconds must be >= 0, "
                f"got {self.base_delay_seconds}"
            )
        if self.growth_factor < 1.0:
            raise ValueError(
                f"RetryConfig.growth_factor must be >= 1.0, "
                f"got {self.growth_factor}"
            )


@dataclass(frozen=True, slots=True)
class ApprovalConfig:
    """Trade-approval gate knobs — REQ_F_NOT_004."""

    timeout_seconds: int = 60
    threshold_amount: Decimal = Decimal("0")
    threshold_currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"ApprovalConfig.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )
        if self.threshold_amount < 0:
            raise ValueError(
                f"ApprovalConfig.threshold_amount must be >= 0, "
                f"got {self.threshold_amount}"
            )
        if self.threshold_currency not in _VALID_CURRENCIES:
            raise ValueError(
                f"ApprovalConfig.threshold_currency must be one of "
                f"{sorted(_VALID_CURRENCIES)}, got {self.threshold_currency!r}"
            )


@dataclass(frozen=True, slots=True)
class SlackChannelConfig:
    """Optional ``notifications.slack`` sub-section.

    Both fields are optional — the channel ships sensible
    defaults so a YAML containing just ``channels: [slack]``
    (with no ``slack:`` sub-section) is valid.
    """

    webhook_url_env: str = DEFAULT_WEBHOOK_URL_ENV
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.webhook_url_env.strip():
            raise ValueError(
                "SlackChannelConfig.webhook_url_env must be non-empty"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"SlackChannelConfig.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )


@dataclass(frozen=True, slots=True)
class EmailChannelConfig:
    """Required ``notifications.email`` sub-section.

    The Email channel has no useful defaults for ``smtp_host`` /
    ``smtp_port`` / ``user`` / ``from_addr`` / ``recipients``, so
    operators enabling the ``email`` channel SHALL provide every
    field. ``password_env`` defaults to the documented env-var
    name; ``use_starttls`` defaults to ``True``.
    """

    smtp_host: str
    smtp_port: int
    user: str
    from_addr: str
    recipients: tuple[str, ...]
    password_env: str = "TRADING_BOT_SMTP_PASSWORD"
    use_starttls: bool = True
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.smtp_host.strip():
            raise ValueError("EmailChannelConfig.smtp_host must be non-empty")
        if not (1 <= self.smtp_port <= 65535):
            raise ValueError(
                f"EmailChannelConfig.smtp_port out of range: {self.smtp_port}"
            )
        if not self.user.strip():
            raise ValueError("EmailChannelConfig.user must be non-empty")
        if not self.from_addr.strip():
            raise ValueError("EmailChannelConfig.from_addr must be non-empty")
        if not self.recipients:
            raise ValueError(
                "EmailChannelConfig.recipients must contain at least one address"
            )
        if not self.password_env.strip():
            raise ValueError("EmailChannelConfig.password_env must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"EmailChannelConfig.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )


@dataclass(frozen=True, slots=True)
class NotificationsConfig:
    """Top-level shape of ``config/notifications.yaml``."""

    channels: tuple[str, ...] = ("local_log",)
    retry: RetryConfig = field(default_factory=RetryConfig)
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)
    local_log_path: str = "var/logs/notifications.jsonl"
    slack: SlackChannelConfig | None = None
    email: EmailChannelConfig | None = None

    def __post_init__(self) -> None:
        if not self.channels:
            raise ValueError(
                "NotificationsConfig.channels must list at least one channel "
                "(LocalLogChannel is the always-available baseline)"
            )
        for ch in self.channels:
            if ch not in _CHANNEL_SELECTORS:
                raise ValueError(
                    f"NotificationsConfig.channels[{ch!r}] not in "
                    f"{sorted(_CHANNEL_SELECTORS)}"
                )
        seen: set[str] = set()
        for ch in self.channels:
            if ch in seen:
                raise ValueError(
                    f"NotificationsConfig.channels has duplicate {ch!r}"
                )
            seen.add(ch)
        if not self.local_log_path.strip():
            raise ValueError(
                "NotificationsConfig.local_log_path must be non-empty"
            )
        # Email channel needs explicit SMTP settings; the dataclass
        # has no useful defaults for them.
        if "email" in self.channels and self.email is None:
            raise ValueError(
                "NotificationsConfig.channels lists 'email' but "
                "notifications.email is missing; supply the SMTP "
                "settings (smtp_host, smtp_port, user, from_addr, "
                "recipients) under notifications.email"
            )


_TOP = "notifications"


def load_notifications_config(
    path: Path | str,
) -> Result[NotificationsConfig, str]:
    """Parse ``config/notifications.yaml``.

    Absent file is NOT an error here — the caller checks ``path.exists()``
    first and falls back to ``NotificationsConfig()`` when it
    doesn't. This loader only fires when the operator has supplied
    a YAML. Empty file ⇒ defaults; missing ``notifications:`` top
    key ⇒ defaults.
    """
    p = Path(path)
    raw_result = catch(lambda: p.read_text(encoding="utf-8"), OSError)
    match raw_result:
        case Err(exc):
            return Err(f"config:io: cannot read {p}: {exc!r}")
        case Ok(text):
            raw_text = text

    parsed_result: Result[Any, BaseException] = catch(
        lambda: yaml.safe_load(raw_text), yaml.YAMLError
    )
    match parsed_result:
        case Err(exc):
            return Err(f"config:parse: invalid YAML at {p}: {exc!r}")
        case Ok(parsed):
            payload = parsed

    if payload is None:
        return Ok(NotificationsConfig())
    if not isinstance(payload, Mapping):
        return Err(
            f"config:schema: top-level of {p} must be a mapping, "
            f"got {type(payload).__name__}"
        )
    section = payload.get(_TOP)
    if section is None:
        return Ok(NotificationsConfig())
    if not isinstance(section, Mapping):
        return Err(
            f"config:schema: '{_TOP}' section must be a mapping, "
            f"got {type(section).__name__} ({p})"
        )

    # ---------- channels --------------------------------------------------
    channels_kwargs: dict[str, Any] = {}
    if "channels" in section:
        ch_raw = section["channels"]
        if not isinstance(ch_raw, list) or not all(
            isinstance(c, str) for c in ch_raw
        ):
            return Err(
                f"config:schema: notifications.channels must be a list of "
                f"strings ({p})"
            )
        channels_kwargs["channels"] = tuple(ch_raw)

    # ---------- retry -----------------------------------------------------
    retry_kwargs: dict[str, Any] = {}
    retry_raw = section.get("retry")
    if retry_raw is not None:
        if not isinstance(retry_raw, Mapping):
            return Err(
                f"config:schema: notifications.retry must be a mapping "
                f"({p})"
            )
        if "max_attempts" in retry_raw:
            v = retry_raw["max_attempts"]
            if not isinstance(v, int) or isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.retry.max_attempts must "
                    f"be int (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["max_attempts"] = v
        if "base_delay_seconds" in retry_raw:
            v = retry_raw["base_delay_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.retry.base_delay_seconds "
                    f"must be numeric (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["base_delay_seconds"] = float(v)
        if "growth_factor" in retry_raw:
            v = retry_raw["growth_factor"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.retry.growth_factor must "
                    f"be numeric (got {type(v).__name__}) ({p})"
                )
            retry_kwargs["growth_factor"] = float(v)
    retry_result = catch(lambda: RetryConfig(**retry_kwargs), ValueError)
    match retry_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(retry):
            pass

    # ---------- approval --------------------------------------------------
    approval_kwargs: dict[str, Any] = {}
    approval_raw = section.get("approval")
    if approval_raw is not None:
        if not isinstance(approval_raw, Mapping):
            return Err(
                f"config:schema: notifications.approval must be a mapping "
                f"({p})"
            )
        if "timeout_seconds" in approval_raw:
            v = approval_raw["timeout_seconds"]
            if not isinstance(v, int) or isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.approval.timeout_seconds "
                    f"must be int (got {type(v).__name__}) ({p})"
                )
            approval_kwargs["timeout_seconds"] = v
        if "threshold_amount" in approval_raw:
            v = approval_raw["threshold_amount"]
            if isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.approval.threshold_amount "
                    f"must be numeric, got bool ({p})"
                )
            try:
                approval_kwargs["threshold_amount"] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                return Err(
                    f"config:schema: notifications.approval.threshold_amount "
                    f"could not be parsed as Decimal (value={v!r}) ({p})"
                )
        if "threshold_currency" in approval_raw:
            v = approval_raw["threshold_currency"]
            if not isinstance(v, str):
                return Err(
                    f"config:schema: notifications.approval.threshold_currency "
                    f"must be a string (got {type(v).__name__}) ({p})"
                )
            approval_kwargs["threshold_currency"] = v
    approval_result = catch(
        lambda: ApprovalConfig(**approval_kwargs), ValueError
    )
    match approval_result:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(approval):
            pass

    # ---------- local_log_path -------------------------------------------
    log_path_kwargs: dict[str, Any] = {}
    if "local_log_path" in section:
        v = section["local_log_path"]
        if not isinstance(v, str):
            return Err(
                f"config:schema: notifications.local_log_path must be a "
                f"string (got {type(v).__name__}) ({p})"
            )
        log_path_kwargs["local_log_path"] = v

    # ---------- slack -----------------------------------------------------
    slack_cfg: SlackChannelConfig | None = None
    slack_raw = section.get("slack")
    if slack_raw is not None:
        if not isinstance(slack_raw, Mapping):
            return Err(
                f"config:schema: notifications.slack must be a mapping ({p})"
            )
        slack_kwargs: dict[str, Any] = {}
        if "webhook_url_env" in slack_raw:
            v = slack_raw["webhook_url_env"]
            if not isinstance(v, str):
                return Err(
                    f"config:schema: notifications.slack.webhook_url_env "
                    f"must be a string ({p})"
                )
            slack_kwargs["webhook_url_env"] = v
        if "timeout_seconds" in slack_raw:
            v = slack_raw["timeout_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.slack.timeout_seconds "
                    f"must be numeric ({p})"
                )
            slack_kwargs["timeout_seconds"] = float(v)
        slack_result = catch(
            lambda: SlackChannelConfig(**slack_kwargs), ValueError
        )
        match slack_result:
            case Err(exc):
                return Err(f"config:invariant: {exc!s} ({p})")
            case Ok(cfg):
                slack_cfg = cfg

    # ---------- email -----------------------------------------------------
    email_cfg: EmailChannelConfig | None = None
    email_raw = section.get("email")
    if email_raw is not None:
        if not isinstance(email_raw, Mapping):
            return Err(
                f"config:schema: notifications.email must be a mapping ({p})"
            )
        email_kwargs: dict[str, Any] = {}
        for key, expected_type, type_name in (
            ("smtp_host", str, "string"),
            ("user", str, "string"),
            ("from_addr", str, "string"),
            ("password_env", str, "string"),
        ):
            if key in email_raw:
                v = email_raw[key]
                if not isinstance(v, expected_type):
                    return Err(
                        f"config:schema: notifications.email.{key} must be "
                        f"a {type_name} (got {type(v).__name__}) ({p})"
                    )
                email_kwargs[key] = v
        if "smtp_port" in email_raw:
            v = email_raw["smtp_port"]
            if isinstance(v, bool) or not isinstance(v, int):
                return Err(
                    f"config:schema: notifications.email.smtp_port must be "
                    f"an int (got {type(v).__name__}) ({p})"
                )
            email_kwargs["smtp_port"] = v
        if "use_starttls" in email_raw:
            v = email_raw["use_starttls"]
            if not isinstance(v, bool):
                return Err(
                    f"config:schema: notifications.email.use_starttls must "
                    f"be a bool (got {type(v).__name__}) ({p})"
                )
            email_kwargs["use_starttls"] = v
        if "timeout_seconds" in email_raw:
            v = email_raw["timeout_seconds"]
            if isinstance(v, bool) or not isinstance(v, (int, float)):  # noqa: UP038
                return Err(
                    f"config:schema: notifications.email.timeout_seconds "
                    f"must be numeric ({p})"
                )
            email_kwargs["timeout_seconds"] = float(v)
        if "recipients" in email_raw:
            v = email_raw["recipients"]
            if not isinstance(v, list) or not all(
                isinstance(r, str) for r in v
            ):
                return Err(
                    f"config:schema: notifications.email.recipients must be "
                    f"a list of strings ({p})"
                )
            email_kwargs["recipients"] = tuple(v)
        email_result = catch(
            lambda: EmailChannelConfig(**email_kwargs), ValueError, TypeError
        )
        match email_result:
            case Err(exc):
                return Err(f"config:invariant: {exc!s} ({p})")
            case Ok(cfg):
                email_cfg = cfg

    built = catch(
        lambda: NotificationsConfig(
            retry=retry,
            approval=approval,
            slack=slack_cfg,
            email=email_cfg,
            **channels_kwargs,
            **log_path_kwargs,
        ),
        ValueError,
    )
    match built:
        case Err(exc):
            return Err(f"config:invariant: {exc!s} ({p})")
        case Ok(cfg):
            return Ok(cfg)


def build_channels(
    config: NotificationsConfig,
    *,
    extra: Sequence[NotificationChannel] = (),
) -> list[NotificationChannel]:
    """Construct the concrete channel instances declared in
    ``config.channels``.

    Used by the runtime (webapp / safety-layer) at boot to wire
    the configured channels into a ``NotificationFanOut``. The
    loader stays the schema gate; this function is the
    schema-to-objects bridge.

    ``extra`` lets callers append additional channels that aren't
    YAML-configured — e.g., the webapp's ``InboxChannel`` which is
    owned by the request/response surface and not by
    ``config/notifications.yaml``. ``extra`` SHALL NOT contain any
    YAML-configured selector under the hood; the caller is
    responsible for not double-subscribing the same backend.

    Iteration order: the YAML selector order is preserved; ``extra``
    channels land after the YAML-configured ones. Deterministic by
    construction so two boots against the same config + same
    ``extra`` produce the same fan-out subscriber order
    (precondition for replay tests).
    """
    channels: list[NotificationChannel] = []
    for selector in config.channels:
        if selector == "local_log":
            channels.append(LocalLogChannel(path=Path(config.local_log_path)))
        elif selector == "slack":
            slack_cfg = config.slack or SlackChannelConfig()
            channels.append(
                SlackNotificationChannel(
                    webhook_url_env=slack_cfg.webhook_url_env,
                    timeout_seconds=slack_cfg.timeout_seconds,
                )
            )
        elif selector == "email":
            # Pre-validated by NotificationsConfig.__post_init__ —
            # email selector ⇒ config.email is not None.
            email_cfg = config.email
            assert email_cfg is not None  # invariant: post_init enforces
            channels.append(
                EmailNotificationChannel(
                    smtp_host=email_cfg.smtp_host,
                    smtp_port=email_cfg.smtp_port,
                    user=email_cfg.user,
                    from_addr=email_cfg.from_addr,
                    recipients=email_cfg.recipients,
                    password_env=email_cfg.password_env,
                    use_starttls=email_cfg.use_starttls,
                    timeout_seconds=email_cfg.timeout_seconds,
                )
            )
        else:
            # _CHANNEL_SELECTORS enforces membership at config-construction
            # time — any path here is a programmer-error invariant.
            raise RuntimeError(
                f"build_channels: unknown selector {selector!r} (config "
                "validation drift)"
            )
    channels.extend(extra)
    return channels
