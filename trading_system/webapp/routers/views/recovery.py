"""Kill-switch recovery wizard view (REQ_F_WEB2_007 + REQ_S_KS_009).

Two routes:
- GET  /operator/recovery  — show informational page OR the wizard
  depending on the current ``KillSwitchState``.
- POST /operator/recovery  — server-side double-check on the
  RecoveryConditions checkboxes + operator-token submission.

The wizard's submit button is DOM-disabled until every checkbox
is checked (no JS-only enforcement per the SDS), and the
server-side handler ALSO refuses to submit when any checkbox is
unset — returning the categorised ``safety:recovery_conditions_unmet``
Err per REQ_S_KS_009.

The recovery gate Protocol is attached to ``app.state.recovery_gate``;
the view never imports ``trading_system.safety`` directly so the
structural audit stays clean.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Protocol, runtime_checkable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from trading_system.result import Err
from trading_system.webapp.auth_deps import _extract_token, verify_any_valid_claim


router = APIRouter(prefix="/operator")


@runtime_checkable
class RecoveryGate(Protocol):
    """Protocol-shaped recovery surface attached to ``app.state``.

    Operators outside the webapp build a closure that delegates to
    ``trading_system.safety.state_manager.StateManager.request_recovery``
    (or any equivalent). The webapp never imports the concrete
    safety types.
    """

    def state(self) -> str:
        """Return one of ``"ACTIVE"`` / ``"DEGRADED"`` / ``"KILL"``."""
        ...

    def last_trigger(self) -> str | None:
        """Categorised trigger code that last fired (or ``None``)."""
        ...

    def request_recovery(
        self,
        *,
        token: str,
        drawdown_recovered: bool,
        integrity_restored: bool,
        backtests_stable: bool,
        at: datetime,
    ) -> object:  # Result[None, str] (avoid importing Result here)
        ...


def _gate(request: Request) -> RecoveryGate | None:
    return getattr(request.app.state, "recovery_gate", None)


def _require_auth(request: Request) -> bool:
    verifier = getattr(request.app.state, "token_verifier", None)
    token = _extract_token(request)
    if (
        verifier is None
        or token is None
        or not verify_any_valid_claim(verifier, token)
    ):
        return False
    return True


def _render(
    request: Request,
    *,
    ks_state: str,
    last_trigger: str | None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="recovery.html",
        context={
            "ks_state": ks_state,
            "last_trigger": last_trigger,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/recovery", response_class=HTMLResponse, name="operator-recovery")
def get_recovery(request: Request):
    """Render the wizard or the informational page based on state."""
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    gate = _gate(request)
    if gate is None:
        # No safety wiring in this deploy — show the informational
        # page so the operator isn't stuck on a 500.
        return _render(request, ks_state="ACTIVE", last_trigger=None)
    return _render(
        request,
        ks_state=str(gate.state()),
        last_trigger=gate.last_trigger(),
    )


@router.post("/recovery", response_class=HTMLResponse, name="operator-recovery-submit")
async def post_recovery(
    request: Request,
    operator_token: Annotated[str, Form()],
    drawdown_recovered: Annotated[str | None, Form()] = None,
    integrity_restored: Annotated[str | None, Form()] = None,
    backtests_stable: Annotated[str | None, Form()] = None,
):
    if not _require_auth(request):
        return RedirectResponse(url="/login", status_code=303)
    gate = _gate(request)
    if gate is None:
        return _render(
            request,
            ks_state="ACTIVE",
            last_trigger=None,
            error="safety:no_recovery_gate_wired",
            status_code=503,
        )

    drawdown_ok = drawdown_recovered == "on"
    integrity_ok = integrity_restored == "on"
    backtests_ok = backtests_stable == "on"
    if not (drawdown_ok and integrity_ok and backtests_ok):
        return _render(
            request,
            ks_state=str(gate.state()),
            last_trigger=gate.last_trigger(),
            error="safety:recovery_conditions_unmet",
            status_code=400,
        )

    result = gate.request_recovery(
        token=operator_token,
        drawdown_recovered=drawdown_ok,
        integrity_restored=integrity_ok,
        backtests_stable=backtests_ok,
        at=datetime.now(tz=UTC),
    )
    if isinstance(result, Err):
        return _render(
            request,
            ks_state=str(gate.state()),
            last_trigger=gate.last_trigger(),
            error=result.error,
            status_code=400,
        )
    # Success — KS state should now be ACTIVE.
    return _render(
        request,
        ks_state=str(gate.state()),
        last_trigger=gate.last_trigger(),
    )
