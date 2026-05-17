"""Generate the ``var/reports/2024-baseline/`` directory shipped in the repo
so a fresh clone can see the expected output shape before running anything.

CR-016 MVP-6 — the Quickstart guide points operators at the baseline so they
can decide whether the report shape is useful *before* installing
``[reports]`` extras and running their own backtest.

Determinism contract:
- Inputs are 100% bundled (no network, no clock, no env): the 3 fixture
  symbols ASML.AS / BNP.PA / SAN.PA from ``data/yfinance-fixtures/``.
- The equity curve is a deterministic equal-weight buy-and-hold across
  the three symbols' weekday closes, starting from EUR 10 000 cash and
  splitting it equally on the first weekday of 2024-01-02.
- No trades are emitted (this is a pure passive baseline; the goal is
  to show the 5-file artefact shape, not to recommend a strategy).
- ``data_provider`` is recorded as ``yfinance-bundled-fixtures`` so the
  manifest is honest about what produced the curve.

Regenerate with::

    .venv/bin/python tools/generate_baseline_report.py

The script overwrites the existing ``var/reports/2024-baseline/`` so a
re-run lands byte-identical output (matplotlib version permitting per
REQ_NF_RPT_001).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trading_system.analytics.report import write_report
from trading_system.backtesting.result import BacktestResult
from trading_system.data.yfinance.bundled import (
    DEFAULT_FIXTURE_ROOT,
    populate_cache_from_bundled_fixtures,
)
from trading_system.data.yfinance.cache import CacheKey, YFinanceCache
from trading_system.models.flow import EquityPoint
from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok


_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_DIR = _REPO_ROOT / "var" / "reports" / "2024-baseline"
_SYMBOLS = ("ASML.AS", "BNP.PA", "SAN.PA")
_STARTING_CASH = Decimal("10000")
_WINDOW_START = datetime(2024, 1, 2, tzinfo=UTC)
_WINDOW_END = datetime(2024, 12, 31, tzinfo=UTC)


def _load_close_series(cache: YFinanceCache) -> dict[str, list[tuple[datetime, Decimal]]]:
    series: dict[str, list[tuple[datetime, Decimal]]] = {}
    for symbol in _SYMBOLS:
        key = CacheKey(
            symbol=symbol, timeframe="1d", start=_WINDOW_START, end=_WINDOW_END
        )
        bars_opt = cache.get_bars(key)
        bars = bars_opt.unwrap()
        series[symbol] = [(b.at, Decimal(str(b.close))) for b in bars]
    return series


def _build_curve(
    series: dict[str, list[tuple[datetime, Decimal]]],
) -> tuple[tuple[EquityPoint, ...], tuple[Decimal, ...]]:
    """Equal-weight buy-and-hold curve.

    Each leg gets ``starting / N`` of cash at the first weekday's close,
    so quantity = (starting / N) / close[0]. Equity at tick t is the
    sum across legs of quantity × close[t]. We align ticks by index —
    fixture bars are weekday-aligned and identical in length across the
    three symbols (≈261 bars in 2024).
    """
    leg_count = len(series)
    cash_per_leg = _STARTING_CASH / leg_count
    quantities: dict[str, Decimal] = {}
    for symbol, points in series.items():
        first_close = points[0][1]
        quantities[symbol] = (cash_per_leg / first_close).quantize(Decimal("0.000001"))

    n_ticks = min(len(points) for points in series.values())
    timeline = series[_SYMBOLS[0]][:n_ticks]

    curve_points: list[EquityPoint] = []
    series_after_tax: list[Decimal] = []
    peak = _STARTING_CASH
    for i in range(n_ticks):
        at = timeline[i][0]
        total = Decimal("0")
        for symbol in _SYMBOLS:
            close = series[symbol][i][1]
            total += quantities[symbol] * close
        total = total.quantize(Decimal("0.01"))
        peak = max(peak, total)
        drawdown = ((peak - total) / peak).quantize(Decimal("0.000001"))
        curve_points.append(
            EquityPoint(
                at=at,
                equity_gross=Money(total, Currency.EUR),
                equity_after_tax=Money(total, Currency.EUR),
                drawdown_pct=drawdown,
            )
        )
        series_after_tax.append(total)

    return tuple(curve_points), tuple(series_after_tax)


def _build_result() -> BacktestResult:
    cache_root = _REPO_ROOT / "var" / "baseline-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    populate_cache_from_bundled_fixtures(
        cache_root=cache_root, fixture_root=DEFAULT_FIXTURE_ROOT
    ).unwrap()
    cache = YFinanceCache(root=cache_root)
    series = _load_close_series(cache)
    curve, excl = _build_curve(series)

    return BacktestResult(
        trades=(),
        equity_curve=curve,
        equity_excl_injections=excl,
        final_cash=Money(_STARTING_CASH, Currency.EUR),
        final_equity_after_tax=Money(excl[-1], Currency.EUR),
        realized_gross=Money(Decimal("0"), Currency.EUR),
        realized_after_tax=Money(Decimal("0"), Currency.EUR),
        dividends_gross=Money(Decimal("0"), Currency.EUR),
        dividends_after_tax=Money(Decimal("0"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
    )


def main() -> int:
    if _OUT_DIR.exists():
        shutil.rmtree(_OUT_DIR)
    result = _build_result()
    res = write_report(
        result,
        config_hash="baseline-2024-eu-dividend-starter",
        out_dir=_OUT_DIR,
        seed=0,
        start_at=_WINDOW_START,
        end_at=_WINDOW_END,
        data_provider="yfinance-bundled-fixtures",
    )
    match res:
        case Ok():
            pass
        case Err(reason):
            print(f"write_report failed: {reason}")
            return 1
    print(f"wrote {_OUT_DIR.relative_to(_REPO_ROOT)} (5 files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
