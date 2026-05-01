"""trading-bot — production-grade Python trading system.

Optimizes after-tax returns under France CTO/PFU taxation, scaling capital
through six gated phases. The architecture follows a layered, downward-only
dependency graph (REQ_NF_TRC_001, REQ_SDS_ARC_001) with engines as pure
functions and adapters at the edges (REQ_SDS_ARC_002).

The runtime modules live here. The bounded research engine (`strategy_lab/`)
is offline-only; the runtime imports only its read-only registry
(REQ_SDS_MOD_014, REQ_SDD_IMP_005).

See:
- ``CLAUDE.md`` — agent guidance and hard rules
- ``Documentations/`` (wiki submodule) — SRS / SDS / SDD / Test Plan
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
