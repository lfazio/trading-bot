"""Closed ``OverlayError`` category set.

REQ refs: REQ_F_HOV_001..005, REQ_SDD_HOV_001 (categorised Errs).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OverlayError:
    """Categorised error for the hedge-overlay subsystem.

    ``category`` SHALL be one of:
      - ``hov:insufficient_history:<observed>/<required>`` —
        ``compute_portfolio_beta`` lacks enough observations.
      - ``hov:degenerate_benchmark`` — benchmark variance is zero.
      - ``hov:phase_below_6`` — the sizer was invoked sub-phase-6
        (informational; the sizer returns ``Ok(())`` instead — this
        category is reserved for callers that want an explicit Err).
      - ``hov:band_satisfied`` — current beta within the policy band
        (informational; same reservation as above).
      - ``hov:cap_exceeded`` — raw notional exceeded the policy cap
        (informational — the sizer clamps and returns the clamped
        notional; reserved for diagnostic callers).
      - ``hov:not_found:<id>`` — ledger ``close`` on a missing id.
      - ``hov:already_closed:<id>`` — ledger ``close`` on a closed row.
    """

    category: str
    detail: str = ""
