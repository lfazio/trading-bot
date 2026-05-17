"""Tests for the two reference routes — live state + registry
promotion.

REQ refs: REQ_F_WEB_002 (live state read), REQ_F_WEB_004 (registry
promotion mutation), REQ_F_WEB_005 (auth), REQ_F_WEB_006
(notification fan-out on success), REQ_F_WEB_008 (idempotency),
REQ_SDS_WEB_002 (mutation delegates to RegistryRepository — no
inlined semantics), REQ_SDD_WEB_002 (WebAuth shape),
REQ_SDD_WEB_003 (promotion mapping to HTTP status codes),
REQ_NF_WEB_002 (byte-identical replay)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from trading_system.accounts.token_verifier import (
    HOUSEHOLD_CLAIM,
    AccountScopedTokenVerifier,
)
from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import Phase
from trading_system.models.safety import KillSwitchState
from trading_system.notifications.fanout import (
    NotificationFanOut,
    RetryPolicy,
)
from trading_system.notifications.channels.local_log import (
    MemoryNotificationChannel,
)
from trading_system.notifications.payloads import AnomalyAlert
from trading_system.result import Err, Ok, Result
from trading_system.webui.auth import WebAuth
from trading_system.webui.idempotency import InMemoryIdempotencyStore
from trading_system.webui.routes.live_state import (
    LiveStateReader,
    build_live_state_handler,
)
from trading_system.webui.routes.registry_promotion import (
    PromotionAuditNotifier,
    RegistryPromoter,
    build_promotion_handler,
)
from trading_system.webui.schemas import LiveStateResponse
from trading_system.webui.server import Request


_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _auth() -> tuple[WebAuth, AccountScopedTokenVerifier]:
    v = AccountScopedTokenVerifier(
        secret=b"shh", ttl_seconds=300, _clock=lambda: _NOW
    )
    return WebAuth(verifier=v), v


@dataclass(slots=True)
class _StubReader:
    state: LiveStateResponse

    def live_state(
        self, *, account_id: AccountId, as_of: datetime
    ) -> LiveStateResponse:
        return self.state


@dataclass(slots=True)
class _StubPromoter:
    outcome: Result[None, str] = field(default_factory=lambda: Ok(None))
    calls: list[tuple[StrategyId, str, str, str, AccountId]] = field(
        default_factory=list
    )

    def promote(
        self,
        *,
        strategy_id: StrategyId,
        operator_token: str,
        operator_id: str,
        rationale: str,
        account_id: AccountId,
    ) -> Result[None, str]:
        self.calls.append(
            (strategy_id, operator_token, operator_id, rationale, account_id)
        )
        return self.outcome


def _live_state() -> LiveStateResponse:
    return LiveStateResponse(
        account_id=AccountId("alpha"),
        as_of=_NOW,
        ks_state=KillSwitchState.ACTIVE,
        phase=Phase.TWO,
        open_positions_count=3,
        equity_after_tax=Decimal("12345.67"),
    )


# ---------------------------------------------------------------------------
# Live-state read endpoint
# ---------------------------------------------------------------------------


def test_live_state_happy_path() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_live_state_handler(
        auth=auth, reader=_StubReader(state=_live_state())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/live-state",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 200
    obj = json.loads(resp.body)
    assert obj["account_id"] == "alpha"
    assert obj["open_positions_count"] == 3


def test_live_state_rejects_per_account_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler = build_live_state_handler(
        auth=auth, reader=_StubReader(state=_live_state())
    )
    resp = handler(
        Request(
            method="GET",
            path="/accounts/alpha/live-state",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert resp.status_code == 401
    assert json.loads(resp.body)["error"] == "registry:token_invalid"


def test_live_state_rejects_wrong_method() -> None:
    auth, _v = _auth()
    handler = build_live_state_handler(
        auth=auth, reader=_StubReader(state=_live_state())
    )
    resp = handler(
        Request(method="POST", path="/accounts/alpha/live-state", headers={})
    )
    assert resp.status_code == 405


def test_live_state_rejects_malformed_path() -> None:
    auth, _v = _auth()
    handler = build_live_state_handler(
        auth=auth, reader=_StubReader(state=_live_state())
    )
    resp = handler(Request(method="GET", path="/bogus", headers={}))
    assert resp.status_code == 400


def test_live_state_byte_identical_replay() -> None:
    """REQ_NF_WEB_002 — identical inputs ⇒ identical bytes."""
    auth, verifier = _auth()
    token = verifier.issue(account_id=HOUSEHOLD_CLAIM, now=_NOW)
    handler = build_live_state_handler(
        auth=auth, reader=_StubReader(state=_live_state())
    )
    req = Request(
        method="GET",
        path="/accounts/alpha/live-state",
        headers={"Authorization": f"Bearer {token}"},
    )
    a = handler(req)
    b = handler(req)
    assert a.body == b.body


# ---------------------------------------------------------------------------
# Registry-promotion endpoint
# ---------------------------------------------------------------------------


def _promotion_handler(
    *,
    auth: WebAuth,
    promoter: RegistryPromoter | None = None,
    idempotency: InMemoryIdempotencyStore | None = None,
):
    ch = MemoryNotificationChannel()
    fan = NotificationFanOut(
        channels=(ch,),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    handler = build_promotion_handler(
        auth=auth,
        promoter=promoter or _StubPromoter(),
        idempotency=idempotency or InMemoryIdempotencyStore(),
        notifier=fan,
    )
    return handler, ch


def _promote_request(
    *,
    token: str = "",
    operator_token: str = "op-token-1",
    account_id: str = "alpha",
    idem_key: str = "",
) -> Request:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if operator_token:
        headers["X-Operator-Token"] = operator_token
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return Request(
        method="POST",
        path="/strategies/core_v3/promote",
        headers=headers,
        body=json.dumps(
            {
                "account_id": account_id,
                "operator_id": "laurent",
                "rationale": "outperformed validation OOS by 1.5x",
            }
        ).encode("utf-8"),
    )


def test_promote_happy_path() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    promoter = _StubPromoter()
    handler, ch = _promotion_handler(auth=auth, promoter=promoter)
    resp = handler(_promote_request(token=token))
    assert resp.status_code == 200
    obj = json.loads(resp.body)
    assert obj == {
        "account_id": "alpha",
        "promoted": True,
        "strategy_id": "core_v3",
    }
    assert len(promoter.calls) == 1
    # The fan-out dispatched an AnomalyAlert (REQ_F_WEB_006).
    assert len(ch.delivered) == 1
    alert = ch.delivered[0]
    assert isinstance(alert, AnomalyAlert)
    assert alert.code == "webui:registry_promotion"


def test_promote_rejects_wrong_method() -> None:
    auth, _v = _auth()
    handler, _ch = _promotion_handler(auth=auth)
    resp = handler(
        Request(method="GET", path="/strategies/core_v3/promote", headers={})
    )
    assert resp.status_code == 405


def test_promote_rejects_malformed_path() -> None:
    auth, _v = _auth()
    handler, _ch = _promotion_handler(auth=auth)
    resp = handler(Request(method="POST", path="/bogus", headers={}, body=b"{}"))
    assert resp.status_code == 400


def test_promote_rejects_missing_body_fields() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler, _ch = _promotion_handler(auth=auth)
    resp = handler(
        Request(
            method="POST",
            path="/strategies/core_v3/promote",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Operator-Token": "op-1",
            },
            body=b'{"account_id":"alpha"}',  # missing operator_id + rationale
        )
    )
    assert resp.status_code == 400
    assert "webui:bad_request_body" in json.loads(resp.body)["error"]


def test_promote_rejects_unauthorised() -> None:
    auth, _v = _auth()
    handler, _ch = _promotion_handler(auth=auth)
    resp = handler(_promote_request())  # no Bearer token
    assert resp.status_code == 401


def test_promote_rejects_missing_operator_token() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    handler, _ch = _promotion_handler(auth=auth)
    resp = handler(_promote_request(token=token, operator_token=""))
    assert resp.status_code == 400
    assert json.loads(resp.body)["error"] == "webui:missing_operator_token"


def test_promote_maps_registry_err_to_http_status() -> None:
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    promoter = _StubPromoter(outcome=Err("registry:already_promoted"))
    handler, _ch = _promotion_handler(auth=auth, promoter=promoter)
    resp = handler(_promote_request(token=token))
    assert resp.status_code == 409
    assert json.loads(resp.body)["error"] == "registry:already_promoted"


def test_promote_idempotency_replay() -> None:
    """REQ_F_WEB_008 — same Idempotency-Key returns the prior
    response body byte-identically, without re-executing
    ``promoter.promote``."""
    auth, verifier = _auth()
    token = verifier.issue(account_id="alpha", now=_NOW)
    promoter = _StubPromoter()
    idem = InMemoryIdempotencyStore()
    handler, _ch = _promotion_handler(
        auth=auth, promoter=promoter, idempotency=idem
    )
    first = handler(_promote_request(token=token, idem_key="abc"))
    second = handler(_promote_request(token=token, idem_key="abc"))
    assert first.body == second.body
    # promoter.promote SHALL NOT fire twice.
    assert len(promoter.calls) == 1


def test_promote_notifier_protocol_conformance() -> None:
    """The notifier param accepts NotificationFanOut AND any
    PromotionAuditNotifier — verify Protocol assertion."""
    fan = NotificationFanOut(
        channels=(MemoryNotificationChannel(),),
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.0),
        sleep=lambda _t: None,
    )
    assert isinstance(fan, PromotionAuditNotifier)


def test_stub_promoter_satisfies_protocol() -> None:
    assert isinstance(_StubPromoter(), RegistryPromoter)


def test_stub_reader_satisfies_protocol() -> None:
    assert isinstance(_StubReader(state=_live_state()), LiveStateReader)
