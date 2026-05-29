"""``LiveTradingRuntime`` — CR-019 step 2 broker-agnostic surface.

Mirrors ``PaperTradingRuntime`` but:
1. The broker is the operator-configured concrete ``BrokerAdapter``
   implementation (REQ_F_BRK_003) — the runtime SHALL NOT branch on
   adapter identity (REQ_SDD_LIV_002).
2. Every order persists to ``live_orders`` BEFORE
   ``BrokerAdapter.submit`` is called (REQ_F_LIV_007 / REQ_SDD_LIV_003).
   A crash between the pre-submit row and the broker call leaves the
   row in ``status="pending"`` for operator reconciliation.
3. The runtime consults ``SafetyLayer.must_halt(account_id)`` BEFORE
   submitting (REQ_F_LIV_006).

REQ refs: REQ_F_LIV_001, REQ_F_LIV_004, REQ_F_LIV_006, REQ_F_LIV_007,
REQ_NF_LIV_001, REQ_SDD_LIV_001..007.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.execution.adapter import BrokerAdapter
from trading_system.models.identifiers import AccountId, OrderId
from trading_system.models.trading import Order
from trading_system.persistence.repositories.live_orders import (
    LiveOrderRepository,
)
from trading_system.result import Err, Nothing, Ok, Option, Result, Some


LIVE_ACCOUNT_PREFIX = "live-"


def new_live_account_id(
    *,
    base_account_id: str = "default",
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
) -> AccountId:
    """REQ_F_LIV_004 — ``live-<account-name>-<utc-iso-timestamp>``.

    The CR-006 base ``AccountId`` (``default`` in single-account
    deployments) sits between the prefix and the timestamp so the
    persistence layer's ``account_id`` column trivially partitions
    live vs paper ledgers (`live-*` vs `paper-*`) AND distinguishes
    which CR-006 account a live session belongs to.
    """
    return AccountId(
        f"{LIVE_ACCOUNT_PREFIX}{base_account_id}-{now().isoformat()}"
    )


# ---------------------------------------------------------------------------
# Safety-layer Protocol — minimal surface the runtime consults
# ---------------------------------------------------------------------------


@runtime_checkable
class SafetyLayerHalt(Protocol):
    """REQ_F_LIV_006 — per-account kill-switch consultation.

    The runtime calls ``must_halt(account_id)`` before every submit.
    Implementations:
    - ``safety.state_manager.SafetyLayer.must_halt`` (production).
    - Test stubs returning a configurable boolean.
    """

    def must_halt(self, account_id: AccountId) -> bool: ...


@dataclass(frozen=True, slots=True)
class _AlwaysActive:
    """Default ``SafetyLayerHalt`` when no kill switch is wired —
    the runtime degrades to "never halt" so single-account / dev
    deployments work without the full safety stack."""

    def must_halt(self, account_id: AccountId) -> bool:
        _ = account_id
        return False


# ---------------------------------------------------------------------------
# Session identity card
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveTradingSession:
    """REQ_SDS_WEB2_005 — frozen identity card for one live session.

    Fields:
    - ``account_id`` — MUST satisfy the ``live-<base>-<iso>`` shape
      so the registry's prefix partition works.
    - ``broker_selector`` — pinned at construction so a config
      rewrite mid-session doesn't silently swap brokers.
    - ``universe`` / ``strategy_id`` — same shape as
      ``PaperTradingSession``.
    """

    account_id: AccountId
    universe: str
    strategy_id: str
    broker_selector: str
    started_at: datetime
    mode_tag: str = "live"

    def __post_init__(self) -> None:
        if not str(self.account_id).startswith(LIVE_ACCOUNT_PREFIX):
            raise ValueError(
                f"LiveTradingSession.account_id must start with "
                f"{LIVE_ACCOUNT_PREFIX!r} (REQ_F_LIV_004); got "
                f"{self.account_id!r}"
            )
        if not self.broker_selector.strip():
            raise ValueError(
                "LiveTradingSession.broker_selector must be non-empty"
            )
        if self.broker_selector == "local":
            raise ValueError(
                "LiveTradingSession.broker_selector SHALL NOT be 'local' "
                "(REQ_F_LIV_002 — live mode requires a concrete live adapter)"
            )
        if not self.universe.strip():
            raise ValueError(
                "LiveTradingSession.universe must be non-empty"
            )
        if not self.strategy_id.strip():
            raise ValueError(
                "LiveTradingSession.strategy_id must be non-empty"
            )
        if self.mode_tag != "live":
            raise ValueError(
                f"LiveTradingSession.mode_tag must be 'live', got "
                f"{self.mode_tag!r}"
            )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LiveTradingRuntime:
    """Live-mode runtime. Composes the operator-configured
    ``BrokerAdapter`` + the live ``LiveOrderRepository`` + the
    per-account ``SafetyLayerHalt``.

    Same external Protocol surface as ``PaperTradingRuntime``
    (REQ_SDD_LIV_001): ``tick_once`` / ``stop`` / ``is_alive``.
    """

    session: LiveTradingSession
    broker: BrokerAdapter
    live_order_repo: LiveOrderRepository
    safety: SafetyLayerHalt = field(default_factory=_AlwaysActive)
    # The runtime SHALL NOT generate `corr_id`s itself — the FastAPI
    # middleware binds one per request; engine ticks driven by the
    # tick-driver task pass it through. ``corr_id_factory`` lets
    # tests inject a deterministic value.
    corr_id_factory: Callable[[], str] = field(
        default_factory=lambda: lambda: ""
    )
    _alive: bool = field(default=True, init=False)
    _orders_submitted: int = field(default=0, init=False)

    def is_alive(self) -> bool:
        return self._alive

    def stop(self) -> None:
        """Idempotent across multiple calls (REQ_SDD_LIV_001)."""
        self._alive = False

    def is_degraded(self) -> bool:
        """v1 stub — the live runtime degrades when the broker
        reports ``broker:not_authenticated`` or
        ``broker:account_disabled``. For the broker-agnostic Phase-5
        slice we return False; the live integration slice wires the
        per-broker degradation surface."""
        return False

    def submit_order(
        self,
        order: Order,
        *,
        submitted_order_json: str,
        now: datetime | None = None,
    ) -> Result[OrderId, str]:
        """The single audited submit path. REQ_SDD_LIV_003 strict
        ordering:

        1. Check ``safety.must_halt(account_id)``.
        2. Call ``live_order_repo.record_submit_intent(...)``.
        3. Call ``broker.submit(order)``.
        4a. On Ok ⇒ ``record_submitted(...)``.
        4b. On Err ⇒ ``record_rejected(...)``.

        ``submitted_order_json`` is a canonical-JSON snapshot of the
        Order at submit-intent time. Callers (the live runtime's tick
        path) build it via the project's canonical serialiser; tests
        pass a stub.
        """
        if not self._alive:
            return Err("live:runtime_stopped")

        # 1. Per-account KS gate.
        if self.safety.must_halt(self.session.account_id):
            return Err("live:account_halted")

        # 2. Pre-submit persistence.
        corr_id = self.corr_id_factory()
        intent = self.live_order_repo.record_submit_intent(
            order_id=order.id,
            account_id=self.session.account_id,
            broker_selector=self.session.broker_selector,
            submitted_order_json=submitted_order_json,
            corr_id=corr_id,
            now=now,
        )
        if isinstance(intent, Err):
            return Err(f"live:persist_intent:{intent.error}")

        # 3. Broker call.
        result = self.broker.submit(order)

        # 4. Post-submit update.
        if isinstance(result, Ok):
            broker_order_id = str(result.value)
            persisted = self.live_order_repo.record_submitted(
                order_id=order.id,
                broker_order_id=broker_order_id,
                account_id=self.session.account_id,
            )
            if isinstance(persisted, Err):
                # The order made it to the broker but our audit row
                # is stale. The runtime SHALL surface this as a
                # categorised Err so the operator can manually
                # reconcile via the dashboard.
                return Err(
                    f"live:persist_submitted:{persisted.error}"
                )
            self._orders_submitted += 1
            return result

        # Err from broker.
        rejection_reason = result.error
        persisted = self.live_order_repo.record_rejected(
            order_id=order.id,
            rejection_reason=rejection_reason,
            account_id=self.session.account_id,
        )
        if isinstance(persisted, Err):
            return Err(
                f"live:persist_rejected:{persisted.error}"
            )
        return Err(f"broker:{rejection_reason}")

    def orders_submitted(self) -> int:
        return self._orders_submitted

    def tick_once(self) -> Result[Option[Order], str]:
        """v1 stub — the broker-agnostic Phase-5 slice ships the
        submit_order audit path + safety gate; the bar-source-driven
        tick loop comes in the broker-integration slice (a concrete
        market-data adapter is required to drive a real tick).
        Tests exercise ``submit_order`` directly; the dashboard's
        SSE channel reads ``orders_submitted()`` + the repository.

        Returns ``Ok(Nothing())`` indicating a no-op tick on the v1
        surface. The signature mirrors ``PaperTradingRuntime`` so
        the dashboard panel can swap runtimes without re-rendering
        (REQ_SDD_LIV_001).
        """
        if not self._alive:
            return Err("live:runtime_stopped")
        if self.safety.must_halt(self.session.account_id):
            return Err("live:account_halted")
        return Ok(Nothing())


# ---------------------------------------------------------------------------
# LiveRuntimeRegistry — process-wide live-session catalogue
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LiveRuntimeRegistry:
    """Process-wide registry of live-trading runtimes
    (REQ_F_PAP_005 extended to live — one ticking session per
    account_id). The id-prefix partition (REQ_F_LIV_004) is enforced
    by ``LiveTradingSession.__post_init__``; this registry holds
    only the ``live-*``-prefixed sessions.
    """

    _live: dict[AccountId, LiveTradingRuntime] = field(default_factory=dict)

    def start(self, runtime: LiveTradingRuntime) -> Result[None, str]:
        if runtime.session.account_id in self._live:
            return Err(
                f"live:already_live:{runtime.session.account_id}"
            )
        if not runtime.is_alive():
            return Err(
                f"live:session_already_stopped:{runtime.session.account_id}"
            )
        self._live[runtime.session.account_id] = runtime
        return Ok(None)

    def stop(self, account_id: AccountId) -> Result[None, str]:
        runtime = self._live.pop(account_id, None)
        if runtime is None:
            return Err(f"live:not_live:{account_id}")
        runtime.stop()
        return Ok(None)

    def status(self, account_id: AccountId) -> Option[LiveTradingRuntime]:
        runtime = self._live.get(account_id)
        if runtime is None:
            return Nothing()
        return Some(runtime)

    def live_account_ids(self) -> tuple[AccountId, ...]:
        return tuple(sorted(self._live.keys()))

    def size(self) -> int:
        return len(self._live)
