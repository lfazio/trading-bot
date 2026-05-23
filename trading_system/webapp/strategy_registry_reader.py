"""Static strategy registry reader for the demo deploy.

REQ refs:
- REQ_F_WEB2_006 — the strategy registry panel lists strategies +
  their lifecycle status + improvement-report blurbs.

This module ships the **demo** reader: a hand-rolled list of the
strategies the wizard's strategy_factory dispatches on (CoreStrategy
+ TacticalStrategy). It produces the same shape the
``StrategyRegistryReader`` Protocol expects, but doesn't reach into
the CR-002 hypothesis library. Production deploys swap in a
reader backed by the SQLite registry once CR-002 Phase B wires it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StaticStrategyRegistryReader:
    """Pure-data reader — emits the documented demo strategies."""

    def list_strategies(self) -> list[dict[str, object]]:
        return [
            {
                "id": "CoreStrategy",
                "status": "validated",
                "last_promoted_at": "",
                "improvement_report": (
                    "Long-term holding, low turnover. Rebalances toward "
                    "the phase's STOCK allocation target when current "
                    "exposure trails by at least the rebalance band. "
                    "Never sells (liquidation is the risk engine's "
                    "stop-loss job)."
                ),
            },
            {
                "id": "TacticalStrategy",
                "status": "validated",
                "last_promoted_at": "",
                "improvement_report": (
                    "Trend / breakout / pullback signals on recent daily "
                    "bars; tight stop-loss; size from the phase's "
                    "per-trade risk-band lower bound."
                ),
            },
        ]
