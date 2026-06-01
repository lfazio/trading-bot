"""CR-032 — operator settings view (REQ_SDD_SET_001).

Three endpoints:

- ``GET /operator/settings`` — landing page with left-nav
  listing the available sub-pages (v1: notifications).
- ``GET /operator/settings/notifications`` — form pre-filled
  from ``config/notifications.yaml``.
- ``POST /operator/settings/notifications`` — save handler;
  validates, atomically writes the YAML, sets
  ``app.state.reload_pending``.

Per the operator-resolved CR-032 questions:
- v1 ships ONE consolidated POST handler per YAML, not the
  six-section split originally drafted. The HTMX form
  groups the sub-sections visually but submits as one
  payload — simpler review surface; sub-section partial
  saves remain a follow-up if operator demand surfaces.
- Validation runs through the existing
  ``NotificationsConfig.__post_init__`` + cross-field check.
- Success ⇒ ``app.state.reload_pending`` set + banner
  rendered on the next chrome paint.
- Atomic write via ``settings_writer.write_notifications_yaml``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.notifications.loader import (
    ApprovalConfig,
    EmailChannelConfig,
    NotificationsConfig,
    RetryConfig,
    SlackChannelConfig,
    load_notifications_config,
)
from trading_system.result import Err, Ok
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim
from trading_system.webapp.fragments import fragment_context
from trading_system.webapp.settings_state import ReloadPending
from trading_system.webapp.settings_writer import (
    env_vars_referenced,
    write_notifications_yaml,
)


router = APIRouter(prefix="/operator/settings")


def _require_auth(request: Request) -> None:
    """Auth gate mirroring the other view routes: any valid
    token claim accepted (per-account OR household per
    REQ_F_SET_001's "settings are household-scoped")."""
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )


def _config_dir(request: Request) -> Path:
    """Resolve the config directory the settings view writes to.

    Honours ``TRADING_BOT_CONFIG_DIR`` for deployments that
    point at a non-default config root; otherwise falls back
    to the repo-bundled ``config/`` directory.
    """
    env_dir = os.environ.get("TRADING_BOT_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    # Resolve relative to the webapp package — same convention
    # as `_default_config_dir()` in `webapp/app.py`.
    return Path(__file__).resolve().parents[3] / "config"


def _env_var_states(names: tuple[str, ...]) -> list[dict]:
    """Render-time helper: each env-var name + whether it's
    set in the current process environment. Used by the banner
    template to show "set" / "unset" indicators.

    REQ_NF_SET_001 — the helper returns the BOOLEAN set/unset
    status only. The resolved value is NEVER returned.
    """
    return [
        {"name": name, "set": bool(os.environ.get(name, "").strip())}
        for name in names
    ]


@router.get("", response_class=HTMLResponse, name="operator-settings")
def get_settings_landing(request: Request):
    """Landing page: left-nav + the active sub-page rendered
    inline. v1 redirects to the notifications sub-page since
    it's the only available editor."""
    try:
        _require_auth(request)
    except HTTPException as e:
        if e.status_code == status.HTTP_303_SEE_OTHER:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return RedirectResponse(
        url="/operator/settings/notifications", status_code=303
    )


@router.get(
    "/notifications",
    response_class=HTMLResponse,
    name="operator-settings-notifications",
)
def get_notifications_settings(request: Request):
    """Render the notifications settings form pre-filled from
    the on-disk YAML. Validation errors from a prior save are
    surfaced via the ``error_field`` / ``error_message`` query
    params (the POST handler redirects back here on failure)."""
    try:
        _require_auth(request)
    except HTTPException as e:
        if e.status_code == status.HTTP_303_SEE_OTHER:
            return RedirectResponse(url="/login", status_code=303)
        raise

    cfg_dir = _config_dir(request)
    notif_path = cfg_dir / "notifications.yaml"
    cfg: NotificationsConfig
    if notif_path.exists():
        result = load_notifications_config(notif_path)
        match result:
            case Ok(loaded):
                cfg = loaded
            case Err(_reason):
                cfg = NotificationsConfig()
    else:
        cfg = NotificationsConfig()

    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise RuntimeError("webapp:templates_missing")

    error_field = request.query_params.get("error_field", "").strip()
    error_message = request.query_params.get("error_message", "").strip()

    return templates.TemplateResponse(
        request=request,
        name="settings_notifications.html",
        context={
            "cfg": cfg,
            "error_field": error_field,
            "error_message": error_message,
            "env_states": _env_var_states(env_vars_referenced(cfg)),
            **fragment_context(request),
        },
    )


@router.post(
    "/notifications",
    response_class=HTMLResponse,
    name="operator-settings-notifications-save",
)
async def post_notifications_settings(request: Request):
    """Save the operator's edits to ``notifications.yaml``.

    On validation failure ⇒ 303-redirect back to the form
    with ``?error_field=<name>&error_message=<reason>`` query
    params so the operator sees the error in context (HTMX
    swap mode renders the form fragment with the error inline).

    On success ⇒ 303-redirect to the form (operator sees the
    new values pre-filled + the reload-pending banner in the
    chrome).
    """
    try:
        _require_auth(request)
    except HTTPException as e:
        if e.status_code == status.HTTP_303_SEE_OTHER:
            return RedirectResponse(url="/login", status_code=303)
        raise

    form = await request.form()

    # ----- Parse form payload into a NotificationsConfig ---------------
    parse_result = _parse_form_into_config(dict(form))
    if isinstance(parse_result, Err):
        field, message = parse_result.error
        return RedirectResponse(
            url=(
                f"/operator/settings/notifications"
                f"?error_field={field}&error_message={message}"
            ),
            status_code=303,
        )
    cfg = parse_result.value

    # ----- Atomic write ---------------------------------------------
    write_result = write_notifications_yaml(_config_dir(request), cfg)
    match write_result:
        case Err(reason):
            return RedirectResponse(
                url=(
                    f"/operator/settings/notifications"
                    f"?error_field=write&error_message={reason}"
                ),
                status_code=303,
            )
        case Ok(_):
            pass

    # ----- Update reload_pending slot --------------------------------
    sections = _sections_from_form(dict(form))
    request.app.state.reload_pending = ReloadPending(
        modified_at=datetime.now(tz=UTC),
        sections_changed=sections,
        env_vars_referenced=env_vars_referenced(cfg),
    )
    return RedirectResponse(
        url="/operator/settings/notifications", status_code=303
    )


def _parse_form_into_config(form: dict):
    """Convert the HTMX form payload into a NotificationsConfig.

    Returns ``Ok(NotificationsConfig)`` on success or
    ``Err((field, message))`` on the first invariant violation
    so the redirect can surface the error inline.
    """
    # Channels: multi-select via repeated checkboxes; FastAPI
    # collapses repeats to comma-separated value or a list. We
    # accept both shapes.
    channels_raw = form.get("channels", "")
    if isinstance(channels_raw, list):
        channels = tuple(str(c).strip() for c in channels_raw if str(c).strip())
    else:
        channels = tuple(
            c.strip() for c in str(channels_raw).split(",") if c.strip()
        )
    if not channels:
        channels = ("local_log",)

    try:
        retry = RetryConfig(
            max_attempts=int(form.get("retry.max_attempts", "3") or "3"),
            base_delay_seconds=float(
                form.get("retry.base_delay_seconds", "0.05") or "0.05"
            ),
            growth_factor=float(
                form.get("retry.growth_factor", "2.0") or "2.0"
            ),
        )
    except (ValueError, TypeError) as e:
        return Err(("retry", str(e)))

    try:
        approval = ApprovalConfig(
            timeout_seconds=int(
                form.get("approval.timeout_seconds", "60") or "60"
            ),
            threshold_amount=Decimal(
                str(form.get("approval.threshold_amount", "0") or "0")
            ),
            threshold_currency=str(
                form.get("approval.threshold_currency", "EUR") or "EUR"
            ).strip(),
        )
    except (ValueError, TypeError, InvalidOperation) as e:
        return Err(("approval", str(e)))

    local_log_path = str(
        form.get("local_log_path", "var/logs/notifications.jsonl")
        or "var/logs/notifications.jsonl"
    ).strip()

    slack: SlackChannelConfig | None = None
    if "slack" in channels:
        try:
            slack = SlackChannelConfig(
                webhook_url_env=str(
                    form.get("slack.webhook_url_env", "TRADING_BOT_SLACK_WEBHOOK_URL")
                    or "TRADING_BOT_SLACK_WEBHOOK_URL"
                ).strip(),
                timeout_seconds=float(
                    form.get("slack.timeout_seconds", "5.0") or "5.0"
                ),
            )
        except (ValueError, TypeError) as e:
            return Err(("slack", str(e)))

    email: EmailChannelConfig | None = None
    if "email" in channels:
        try:
            recipients_raw = form.get("email.recipients", "")
            if isinstance(recipients_raw, list):
                recipients = tuple(
                    str(r).strip() for r in recipients_raw if str(r).strip()
                )
            else:
                recipients = tuple(
                    r.strip()
                    for r in str(recipients_raw).replace(",", "\n").split("\n")
                    if r.strip()
                )
            email = EmailChannelConfig(
                smtp_host=str(form.get("email.smtp_host", "") or "").strip(),
                smtp_port=int(form.get("email.smtp_port", "587") or "587"),
                user=str(form.get("email.user", "") or "").strip(),
                from_addr=str(form.get("email.from_addr", "") or "").strip(),
                recipients=recipients,
                password_env=str(
                    form.get("email.password_env", "TRADING_BOT_SMTP_PASSWORD")
                    or "TRADING_BOT_SMTP_PASSWORD"
                ).strip(),
                use_starttls=bool(form.get("email.use_starttls", "1")),
                timeout_seconds=float(
                    form.get("email.timeout_seconds", "10.0") or "10.0"
                ),
            )
        except (ValueError, TypeError) as e:
            return Err(("email", str(e)))

    try:
        cfg = NotificationsConfig(
            channels=channels,
            retry=retry,
            approval=approval,
            local_log_path=local_log_path,
            slack=slack,
            email=email,
        )
    except ValueError as e:
        return Err(("channels", str(e)))
    return Ok(cfg)


def _sections_from_form(form: dict) -> tuple[str, ...]:
    """Return which sub-sections of the form carry edits.

    v1 over-reports — every section the form submits is
    flagged as edited because the consolidated POST handler
    can't tell which fields actually changed without a diff
    against the on-disk YAML. The follow-up CR that splits
    the handler into per-section endpoints (CR-032 question 4
    follow-up) refines this.
    """
    sections = ["channels", "retry", "approval", "local_log_path"]
    if any(k.startswith("slack.") for k in form):
        sections.append("slack")
    if any(k.startswith("email.") for k in form):
        sections.append("email")
    return tuple(sections)
