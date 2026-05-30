"""Technical-indicator function library (CR-028).

Closed canonical set of pure-function helpers strategies consume at
decision time + the hypothesis runner consumes at evaluation time.
Decimal-only arithmetic so backtest replays remain byte-identical
(REQ_NF_DET_001 / REQ_F_IND_006).

Public surface:
- ``sma(closes, n)`` — simple moving average.
- ``rsi(closes, n=14)`` — Wilder's relative strength index.
- ``atr(bars, n=14)`` — Wilder's average true range.
- ``obv(bars)`` — on-balance volume (cumulative).
- ``adx(bars, n=14)`` — Wilder's average directional index.

REQ refs:
- REQ_F_IND_001 — closed function set exported here.
- REQ_F_IND_002 — fixed-shape ``tuple[Decimal | None, ...]`` return.
- REQ_F_IND_003 — Decimal-only inputs/outputs; no numpy/pandas.
- REQ_F_IND_004 — Wilder smoothing for RSI/ATR/ADX.
- REQ_SDD_IND_001..005 — packaging + return-shape + recurrence +
  determinism contracts.
"""

from trading_system.quant.indicators.adx import adx
from trading_system.quant.indicators.atr import atr
from trading_system.quant.indicators.obv import obv
from trading_system.quant.indicators.rsi import rsi
from trading_system.quant.indicators.sma import sma

__all__ = ["adx", "atr", "obv", "rsi", "sma"]
