"""``SlackNotificationChannel`` — CR-001 Phase B / CR-018.

Posts a Block Kit JSON payload to a Slack incoming-webhook URL via
the stdlib ``urllib.request`` — no ``requests`` / ``aiohttp``
dependency (matches the ``EmailNotificationChannel`` stdlib-only
pattern). The constructor takes the env-var *name*, never the
resolved URL; the URL is read lazily inside ``deliver`` so it
never appears in logs (REQ_NF_NOT_003 / REQ_SDD_NOT_007).

Per-payload Block Kit layouts are deterministic — two deployments
that deliver the same payload produce the same Slack message body.
This matches REQ_NF_NOT_002's canonical-body byte-identical-replay
discipline (the request body is canonical; the Slack API response is
operator-side and not part of the determinism contract).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from trading_system.notifications.payloads import (
    AnomalyAlert,
    Error,
    KillSwitchEvent,
    NotificationPayload,
    Summary,
    TradeApprovalRequest,
)
from trading_system.result import Err, Ok, Result


DEFAULT_WEBHOOK_URL_ENV = "TRADING_BOT_SLACK_WEBHOOK_URL"
"""Env-var name the channel reads the webhook URL from when the
operator doesn't specify one explicitly. Documented in
CR-018's discussion so deployment recipes are predictable."""


_SEVERITY_COLOR: dict[str, str] = {
    "KILL": "#c0392b",        # red
    "DEGRADE": "#e67e22",     # orange
    "RECOVERY": "#27ae60",    # green
    "URGENT": "#c0392b",
    "WARN": "#e67e22",
    "INFO": "#3498db",        # blue
}


@dataclass(slots=True)
class SlackNotificationChannel:
    """REQ_F_NOT_002 — Slack incoming-webhook channel.

    ``webhook_url_env`` is the env-var NAME; the channel resolves it
    every time ``deliver`` runs so a rotated webhook lands without a
    restart. ``timeout_seconds`` bounds the urllib POST so a stalled
    Slack endpoint can't block the fan-out indefinitely; the
    fan-out's own retry handles the categorised Err this channel
    returns on timeout.
    """

    webhook_url_env: str = DEFAULT_WEBHOOK_URL_ENV
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.webhook_url_env.strip():
            raise ValueError(
                "SlackNotificationChannel.webhook_url_env must be non-empty"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"SlackNotificationChannel.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        """REQ_NF_NOT_003 — the webhook URL is read lazily and never
        logged. ``Err`` returns surface the categorised reason; the
        URL itself is held by reference inside this function only."""
        url = os.environ.get(self.webhook_url_env, "").strip()
        if not url:
            return Err(
                f"notifications:slack:webhook_url_env_unset:{self.webhook_url_env}"
            )
        body = render_block_kit(payload)
        encoded = json.dumps(body, sort_keys=True).encode("utf-8")
        try:
            request = UrlRequest(
                url,
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status >= 400:
                    return Err(
                        f"notifications:slack:http_{response.status}"
                    )
        except HTTPError as e:
            return Err(f"notifications:slack:http_{e.code}")
        except URLError as e:
            return Err(f"notifications:slack:url_error:{e.reason!s}")
        except OSError as e:
            return Err(f"notifications:slack:io:{e!s}")
        return Ok(None)


# ---------------------------------------------------------------------------
# Block Kit rendering — closed per-payload layout for determinism.
# ---------------------------------------------------------------------------


def render_block_kit(payload: NotificationPayload) -> dict[str, Any]:
    """Closed mapping payload-kind → Block Kit message body.

    Each branch is pure + deterministic so identical payloads
    produce identical Block Kit JSON (REQ_NF_NOT_002 family).
    """
    if isinstance(payload, KillSwitchEvent):
        return _render_kill_switch(payload)
    if isinstance(payload, TradeApprovalRequest):
        return _render_trade_approval(payload)
    if isinstance(payload, Summary):
        return _render_summary(payload)
    if isinstance(payload, AnomalyAlert):
        return _render_anomaly(payload)
    if isinstance(payload, Error):
        return _render_error(payload)
    # The payload union is closed; the elif chain above is exhaustive.
    return {"text": f"unsupported payload kind: {type(payload).__name__}"}


def _render_kill_switch(p: KillSwitchEvent) -> dict[str, Any]:
    return {
        "text": f"trading-bot KS {p.severity}: {p.trigger_code}",
        "attachments": [
            {
                "color": _SEVERITY_COLOR.get(p.severity, "#3498db"),
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"KS {p.severity} — {p.trigger_code}",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*State*\n{p.state_from.value} → {p.state_to.value}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Snapshot*\n`{p.snapshot_id}`",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": p.summary,
                        },
                    },
                ],
            }
        ],
    }


def _render_trade_approval(p: TradeApprovalRequest) -> dict[str, Any]:
    return {
        "text": f"trading-bot trade approval — {p.instrument} {p.side.value}",
        "attachments": [
            {
                "color": _SEVERITY_COLOR["WARN"],
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"approval request — {p.instrument}",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Account*\n`{p.account_id}`"},
                            {"type": "mrkdwn", "text": f"*Side*\n{p.side.value}"},
                            {"type": "mrkdwn", "text": f"*Quantity*\n{p.quantity}"},
                            {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Expected loss*\n"
                                    f"{p.expected_loss.amount} {p.expected_loss.currency.value}"
                                ),
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"_Rationale:_ {p.rationale_digest}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    f"request `{p.request_id}` · "
                                    f"expires {p.expires_at.isoformat()}"
                                ),
                            }
                        ],
                    },
                ],
            }
        ],
    }


def _render_summary(p: Summary) -> dict[str, Any]:
    exposure_lines = "\n".join(
        f"• {cls.value}: {pct * 100:.2f}%"
        for cls, pct in sorted(p.exposure.items(), key=lambda kv: kv[0].value)
    ) or "_no exposure_"
    realisation_lines = "\n".join(
        f"• {row.instrument}: {row.realized_after_tax.amount} "
        f"{row.realized_after_tax.currency.value}"
        for row in p.top_realizations
    ) or "_no realisations this period_"
    return {
        "text": f"trading-bot {p.schedule} summary — {p.account_id}",
        "attachments": [
            {
                "color": _SEVERITY_COLOR["INFO"],
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{p.schedule} summary — {p.account_id}",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Equity*\n{p.equity_after_tax.amount} "
                                    f"{p.equity_after_tax.currency.value}"
                                ),
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*As of*\n{p.as_of.isoformat()}",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Exposure*\n{exposure_lines}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Top realisations*\n{realisation_lines}",
                        },
                    },
                ],
            }
        ],
    }


def _render_anomaly(p: AnomalyAlert) -> dict[str, Any]:
    return {
        "text": f"trading-bot anomaly — {p.code}",
        "attachments": [
            {
                "color": _SEVERITY_COLOR.get(p.severity, "#3498db"),
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"{p.severity} — {p.code}",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Account*\n`{p.account_id}`",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*At*\n{p.at.isoformat()}",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": p.message},
                    },
                ],
            }
        ],
    }


def _render_error(p: Error) -> dict[str, Any]:
    return {
        "text": f"trading-bot error — {p.code}",
        "attachments": [
            {
                "color": "#7f8c8d",  # grey
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"error — {p.code}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": p.detail},
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"at {p.at.isoformat()}",
                            }
                        ],
                    },
                ],
            }
        ],
    }
