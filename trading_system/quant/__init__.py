"""Quant-layer runtime primitives (CR-028).

Closed indicator function set + supporting Protocols that strategies
and the hypothesis runner consume. NOT to be confused with
``strategy_lab/quant/`` (CR-002) — that one is offline-only research
machinery. This package is **runtime-safe** — both ``backtesting/``
and ``webapp/runtimes/`` MAY import freely.

REQ refs:
- REQ_F_IND_001..006 — closed indicator set, return-shape contract,
  Decimal-only discipline, Wilder smoothing, VolatilityIndexProvider,
  determinism.
- REQ_NF_IND_001 — runtime-safe + structurally allow-listed.
"""
