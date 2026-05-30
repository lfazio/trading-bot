"""Execution layer (L2 adapter) — broker contract and local simulator.

Defines the ``BrokerAdapter`` Protocol and ships ``LocalBrokerAdapter``,
the in-process deterministic broker the lifecycle uses as its
conformance baseline. Live-broker adapters are deferred until a broker
is selected (REQ_F_BRK_003).

REQ refs:
- REQ_F_BRK_001 — ``BrokerAdapter`` interface methods.
- REQ_F_BRK_002 — ``LocalBrokerAdapter`` simulates fills, fees,
  slippage in-process.
- REQ_F_BRK_003 — live broker deferred.
- REQ_F_BRK_004 — adapter selected by configuration (``broker.adapter``).
- REQ_F_BRK_005 — engines depend only on the Protocol.
- REQ_SDS_INT_001 — Protocol surface and conformance test pattern.
- REQ_SDD_API_002 — runtime-checkable Protocol.
- REQ_SDD_API_006 — submit/cancel idempotent on duplicate client ids.
- REQ_SDD_ERR_002 — adapter errors mapped to ``Result[T, str]`` with
  category prefix (``broker:`` / ``network:``).
"""

from trading_system.execution.adapter import BrokerAdapter, Subscription
from trading_system.execution.fees import FeeModel, FlatFeeModel
from trading_system.execution.local import LocalBrokerAdapter
from trading_system.execution.paper import PaperBrokerAdapter
from trading_system.execution.slippage import GaussianSlippageModel, SlippageModel
from trading_system.execution.types import Account, Tick

__all__ = [
    "Account",
    "BrokerAdapter",
    "FeeModel",
    "FlatFeeModel",
    "GaussianSlippageModel",
    "LocalBrokerAdapter",
    "PaperBrokerAdapter",
    "SlippageModel",
    "Subscription",
    "Tick",
]
