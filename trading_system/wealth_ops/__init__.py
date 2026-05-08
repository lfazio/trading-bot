"""Phase-5+ wealth-operations modules.

Houses sub-packages activated only above phase 5 (Wealth
Preservation):

- ``sector_rotator/`` (CR-010) — regime-driven sector tilt with
  holding-period + whipsaw + rotation-cap guards.

Tax-loss harvesting lives in ``trading_system/tax/harvest.py``
(REQ_F_TAX_006); it is shipped at TEST already and not duplicated
here. Future Phase-5 capabilities (currency hedging — CR-011) will
ship under this package once their lifecycle cascades land.
"""
