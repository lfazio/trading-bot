"""``EmailNotificationChannel`` — CR-001 Phase B.

SMTP sender for the bundled adapter set. The constructor takes
the env-var *name* for the SMTP password; the resolved password
is read lazily inside ``deliver`` so it never appears in logs
(REQ_NF_NOT_003 / REQ_SDD_NOT_007).

Implementation uses stdlib ``smtplib`` + ``email.message`` only
— no third-party dependency. The Phase B v1 sends plain-text
bodies (one line per payload field; deterministic rendering for
REQ_NF_NOT_002 byte-identical replay). Phase 7 follow-up adds
HTML multipart if operators want richer formatting; the Protocol
surface stays unchanged.
"""

from __future__ import annotations

import os
import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage

from trading_system.notifications.payloads import (
    AnomalyAlert,
    Error,
    KillSwitchEvent,
    NotificationPayload,
    Summary,
    TradeApprovalRequest,
)
from trading_system.result import Err, Ok, Result


DEFAULT_PASSWORD_ENV = "TRADING_BOT_SMTP_PASSWORD"


@dataclass(slots=True)
class EmailNotificationChannel:
    """REQ_F_NOT_002 — SMTP channel.

    Operator-facing knobs:
    - ``smtp_host`` / ``smtp_port`` — the SMTP server. Port defaults
      to ``587`` (STARTTLS); ``465`` is the implicit-TLS path.
    - ``user`` / ``from_addr`` — SMTP login + envelope FROM.
    - ``password_env`` — env-var NAME holding the SMTP password.
      Default ``TRADING_BOT_SMTP_PASSWORD``. Never a resolved value.
    - ``recipients`` — list of envelope-TO addresses.
    - ``use_starttls`` — STARTTLS on port 587 (default). Set False
      for plain-text test servers only.
    - ``timeout_seconds`` — bounds the SMTP exchange.
    """

    smtp_host: str
    smtp_port: int
    user: str
    from_addr: str
    recipients: Sequence[str]
    password_env: str = DEFAULT_PASSWORD_ENV
    use_starttls: bool = True
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.smtp_host.strip():
            raise ValueError("EmailNotificationChannel.smtp_host must be non-empty")
        if not (1 <= self.smtp_port <= 65535):
            raise ValueError(
                f"EmailNotificationChannel.smtp_port out of range: {self.smtp_port}"
            )
        if not self.user.strip():
            raise ValueError("EmailNotificationChannel.user must be non-empty")
        if not self.from_addr.strip():
            raise ValueError("EmailNotificationChannel.from_addr must be non-empty")
        if not self.recipients:
            raise ValueError(
                "EmailNotificationChannel.recipients must contain at least one address"
            )
        if not self.password_env.strip():
            raise ValueError("EmailNotificationChannel.password_env must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"EmailNotificationChannel.timeout_seconds must be > 0, "
                f"got {self.timeout_seconds}"
            )

    def deliver(self, payload: NotificationPayload) -> Result[None, str]:
        """REQ_NF_NOT_003 — the SMTP password is read lazily and
        never logged. The constructed ``EmailMessage`` carries the
        canonical render body; the SMTP exchange handles transport.
        """
        password = os.environ.get(self.password_env, "").strip()
        if not password:
            return Err(
                f"notifications:email:password_env_unset:{self.password_env}"
            )
        subject, body = render_email(payload)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_addr
        message["To"] = ", ".join(self.recipients)
        message.set_content(body)
        try:
            if self.use_starttls:
                with smtplib.SMTP(
                    self.smtp_host, self.smtp_port, timeout=self.timeout_seconds
                ) as smtp:
                    smtp.starttls()
                    smtp.login(self.user, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(
                    self.smtp_host, self.smtp_port, timeout=self.timeout_seconds
                ) as smtp:
                    smtp.login(self.user, password)
                    smtp.send_message(message)
        except smtplib.SMTPAuthenticationError as e:
            return Err(f"notifications:email:auth:{e.smtp_code}")
        except smtplib.SMTPException as e:
            return Err(f"notifications:email:smtp:{e!s}")
        except OSError as e:
            return Err(f"notifications:email:io:{e!s}")
        return Ok(None)


# ---------------------------------------------------------------------------
# Plain-text rendering — closed per-payload layout for determinism.
# ---------------------------------------------------------------------------


def render_email(payload: NotificationPayload) -> tuple[str, str]:
    """Return ``(subject, body)`` — pure + deterministic so identical
    payloads produce byte-identical emails (REQ_NF_NOT_002 family)."""
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
    return ("trading-bot notification", repr(payload))


def _render_kill_switch(p: KillSwitchEvent) -> tuple[str, str]:
    subject = f"[trading-bot] KS {p.severity} — {p.trigger_code}"
    body = (
        f"Kill-switch event\n"
        f"-----------------\n"
        f"Severity:   {p.severity}\n"
        f"Trigger:    {p.trigger_code}\n"
        f"State:      {p.state_from.value} -> {p.state_to.value}\n"
        f"Snapshot:   {p.snapshot_id}\n"
        f"\n"
        f"{p.summary}\n"
    )
    return subject, body


def _render_trade_approval(p: TradeApprovalRequest) -> tuple[str, str]:
    subject = (
        f"[trading-bot] trade approval — {p.instrument} {p.side.value} {p.quantity}"
    )
    body = (
        f"Trade approval request\n"
        f"----------------------\n"
        f"Request id:     {p.request_id}\n"
        f"Account:        {p.account_id}\n"
        f"Instrument:     {p.instrument}\n"
        f"Side:           {p.side.value}\n"
        f"Quantity:       {p.quantity}\n"
        f"Expected loss:  {p.expected_loss.amount} {p.expected_loss.currency.value}\n"
        f"Requested at:   {p.requested_at.isoformat()}\n"
        f"Expires at:     {p.expires_at.isoformat()}\n"
        f"\n"
        f"Rationale: {p.rationale_digest}\n"
    )
    return subject, body


def _render_summary(p: Summary) -> tuple[str, str]:
    subject = f"[trading-bot] {p.schedule} summary — {p.account_id}"
    exposure_lines = "\n".join(
        f"  {cls.value:<14} {pct * 100:.2f}%"
        for cls, pct in sorted(p.exposure.items(), key=lambda kv: kv[0].value)
    ) or "  (none)"
    realisation_lines = "\n".join(
        f"  {row.instrument:<14} {row.realized_after_tax.amount} "
        f"{row.realized_after_tax.currency.value}  ({row.closed_at.isoformat()})"
        for row in p.top_realizations
    ) or "  (none this period)"
    milestones = ", ".join(p.pending_milestones) if p.pending_milestones else "(none)"
    body = (
        f"{p.schedule.capitalize()} summary — {p.account_id}\n"
        f"-----------------------\n"
        f"Equity:        {p.equity_after_tax.amount} {p.equity_after_tax.currency.value}\n"
        f"As of:         {p.as_of.isoformat()}\n"
        f"\n"
        f"Exposure:\n"
        f"{exposure_lines}\n"
        f"\n"
        f"Top realisations:\n"
        f"{realisation_lines}\n"
        f"\n"
        f"Pending milestones: {milestones}\n"
        f"Last improvement:   {p.last_improvement_digest or '(none)'}\n"
    )
    return subject, body


def _render_anomaly(p: AnomalyAlert) -> tuple[str, str]:
    subject = f"[trading-bot] anomaly — {p.code}"
    body = (
        f"Anomaly alert\n"
        f"-------------\n"
        f"Code:       {p.code}\n"
        f"Severity:   {p.severity}\n"
        f"Account:    {p.account_id}\n"
        f"At:         {p.at.isoformat()}\n"
        f"\n"
        f"{p.message}\n"
    )
    return subject, body


def _render_error(p: Error) -> tuple[str, str]:
    subject = f"[trading-bot] error — {p.code}"
    body = (
        f"Error\n"
        f"-----\n"
        f"Code:      {p.code}\n"
        f"At:        {p.at.isoformat()}\n"
        f"\n"
        f"{p.detail}\n"
    )
    return subject, body
