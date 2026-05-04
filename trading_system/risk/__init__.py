"""Risk engine.

Pre-trade and post-trade gates per SDD §4.5. Pre-trade short-circuits
on the first failure in REQ_SDD_ALG_016 order:
``kill-switch -> risk-per-trade-band -> stop-loss-presence ->
class-cap -> correlation -> regime``. Post-trade evaluates the
after-tax equity curve for drawdown breaches (REQ_SDD_ALG_005) and
the Phase 5+ portfolio-vol cap (REQ_SDD_ALG_009); breaches escalate
to the kill switch.

Single-asset exposure cap (REQ_F_RSK_002) is **deferred to a
follow-up** — it needs a per-asset value method on ``PortfolioView``
that the current stub doesn't expose. Documented limitation; the
config field is parsed and stored so the gate can land later
without touching the YAML.

REQ refs:
- REQ_F_RSK_001..005 — full surface.
- REQ_SDS_MOD_009 / REQ_SDD_ERR_003 — internal inconsistencies
  escalate to a kill-switch INTEGRITY trigger.
- REQ_SDD_ALG_005 — drawdown formula.
- REQ_SDD_ALG_008 — correlation guard (60-day default).
- REQ_SDD_ALG_009 — Phase 5 / 6 vol caps (12 % / 8 %).
- REQ_SDD_ALG_016 — pre-trade gate ordering.
- REQ_SDS_FLO_001 — every trade decision traverses tax -> risk ->
  safety -> broker.
"""

from trading_system.risk.config import RiskConfig
from trading_system.risk.engine import RiskEngine
from trading_system.risk.loader import load_risk_config
from trading_system.risk.mapping import buckets_for_class
from trading_system.risk.metrics import (
    drawdown_now,
    portfolio_vol_ann,
    realized_correlation,
)

__all__ = [
    "RiskConfig",
    "RiskEngine",
    "buckets_for_class",
    "drawdown_now",
    "load_risk_config",
    "portfolio_vol_ann",
    "realized_correlation",
]
