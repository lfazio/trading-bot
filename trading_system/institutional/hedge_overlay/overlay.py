"""``HedgeOverlay.size`` — pure sizer; phase gate first.

REQ refs: REQ_F_HOV_003, REQ_SDS_HOV_002, REQ_SDD_HOV_002.

The sizer emits **at most one** ``OverlayProposal`` per call. Phase
gating happens BEFORE any other inputs are read — sub-phase-6 calls
return ``Ok(())`` unconditionally (informational ``hov:phase_below_6``
is reserved for callers that prefer explicit Err).

The clamp ``notional = min(raw_notional, max_overlay_pct ×
household_equity)`` enforces the REQ_F_CAP_011 hard ceiling. With
``current_beta=2.0`` / ``target_beta=0.5`` / ``hedge_ratio=1.0`` /
``household_equity=1_000_000`` / ``max_overlay_pct=0.10`` the
emitted notional is exactly ``100_000`` (TC_HOV_006).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.institutional.hedge_overlay.errors import OverlayError
from trading_system.institutional.hedge_overlay.instruments import OverlayProposal
from trading_system.institutional.hedge_overlay.policy import OverlayPolicy
from trading_system.result import Ok, Result


@dataclass(slots=True)
class HedgeOverlay:
    """Pure sizer — instances carry no mutable state; one shared
    instance is enough."""

    def size(
        self,
        *,
        current_beta: Decimal,
        policy: OverlayPolicy,
        phase: int,
        household_equity: Decimal,
    ) -> Result[tuple[OverlayProposal, ...], OverlayError]:
        # Phase gate FIRST — REQ_SDS_HOV_002 / REQ_SDD_HOV_002.
        # The sizer SHALL NOT inspect any other input until phase >= 6
        # so the structural test can drive the Cartesian product of
        # bogus inputs against the phase gate.
        if phase < 6:
            return Ok(())

        # Band check — REQ_F_HOV_003.
        beta_delta = current_beta - policy.target_beta
        if abs(beta_delta) <= policy.beta_band:
            return Ok(())

        # Sizing math (REQ_F_HOV_003).
        raw_notional = abs(beta_delta) * household_equity * policy.hedge_ratio
        cap = policy.max_overlay_pct * household_equity
        notional = min(raw_notional, cap)

        # OverlayProposal invariant: notional > 0. Skip emission when
        # the clamp + zero-equity edge case produced a non-positive
        # notional (defensive — callers SHALL NOT pass 0 equity, but
        # the guard keeps the row constructor's invariant intact).
        if notional <= 0:
            return Ok(())

        side: Literal["short", "long"] = "short" if beta_delta > 0 else "long"
        return Ok(
            (
                OverlayProposal(
                    benchmark=policy.benchmark,
                    side=side,
                    notional=notional,
                    target_beta_delta=beta_delta,
                    cadence=policy.rebalance_frequency,
                ),
            )
        )
