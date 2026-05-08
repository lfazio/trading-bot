"""End-to-end demo: connect (mock) -> screener -> trades -> phase -> portfolio -> after-tax results.

REQ refs:
- REQ_O_001 — runnable Python project; no missing modules.
- REQ_O_002 — main.py demonstrates the full pipeline.
- REQ_O_003 — starting capital, broker selection, and phase
  thresholds read from configuration.

Usage::

    python -m trading_system.main \
        --config-dir config/ \
        --start 2026-01-01 \
        --end   2026-04-01

The default invocation reads configs from ``config/``, builds a
small EU dividend universe with hand-registered fundamentals, runs
the deterministic mock data provider, drives a single
``CoreStrategy`` through the backtest, and prints an after-tax
summary plus the headline dashboard view.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_system.analytics import Analytics
from trading_system.backtesting import Backtest, BacktestConfig
from trading_system.dashboard import Dashboard, DashboardView
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Fundamentals, Timeframe
from trading_system.execution.fees import FlatFeeModel
from trading_system.execution.slippage import GaussianSlippageModel, ZeroSlippageModel
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket, MarketRegime, Phase
from trading_system.models.safety import KillSwitchState, KillSwitchTrigger
from trading_system.models.trading import Dividend
from trading_system.phase_engine.loader import load_phase_engine
from trading_system.result import Err, Ok, Result
from trading_system.risk.engine import RiskEngine
from trading_system.risk.loader import load_risk_config
from trading_system.screener import ScreenerConfig, screen
from trading_system.strategies.core import CoreStrategy, CoreStrategyConfig
from trading_system.tax.config import TaxConfig

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_START = datetime(2026, 1, 1, tzinfo=UTC)
DEFAULT_END = datetime(2026, 4, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _SystemConfig:
    """Subset of system.yaml the demo reads. Frozen so the
    runtime can't accidentally mutate it (REQ_SDS_INT_004)."""

    starting_capital: Money
    seed: int
    mode: str
    broker_adapter: str


# ----------------------------------------------------------------------
# Configuration loading
# ----------------------------------------------------------------------


def _load_system_config(path: Path) -> Result[_SystemConfig, str]:  # noqa: PLR0911
    """Parse system.yaml. Returns a categorised Err on any failure."""
    try:
        text = path.read_text()
    except OSError as e:
        return Err(f"main:system_config_read:{path}:{e}")
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return Err(f"main:system_config_yaml:{path}:{e}")
    if not isinstance(raw, dict):
        return Err(f"main:system_config_shape:{path}: expected a mapping at top level")
    sys_section = raw.get("system", {})
    broker_section = raw.get("broker", {})
    capital = sys_section.get("starting_capital", {})
    if not isinstance(capital, dict):
        return Err("main:system_config:starting_capital must be a mapping")
    amount = capital.get("amount")
    currency = capital.get("currency")
    if amount is None or currency is None:
        return Err("main:system_config:starting_capital.amount/currency required")
    try:
        cur = Currency(currency)
    except ValueError as e:
        return Err(f"main:system_config:bad_currency:{currency}:{e}")
    return Ok(
        _SystemConfig(
            starting_capital=Money(Decimal(str(amount)), cur),
            seed=int(sys_section.get("seed", 0)),
            mode=str(sys_section.get("mode", "backtest")),
            broker_adapter=str(broker_section.get("adapter", "local")),
        )
    )


# ----------------------------------------------------------------------
# Demo universe — hand-registered fundamentals + dividends so the
# mock provider is screener-compatible without touching the network.
# ----------------------------------------------------------------------


def _build_universe(
    data: MockMarketDataProvider,
    currency: Currency,
) -> list[Stock]:
    """Register fundamentals for a small EU dividend universe and
    return the stock list."""
    seed_universe: list[tuple[Stock, Fundamentals, Decimal]] = [
        (
            _stock("ASML", "ASML.AS", "AS", "NL0010273215", "tech", "NL"),
            Fundamentals(
                yield_=Decimal("0.045"),
                payout_ratio=Decimal("0.55"),
                free_cash_flow=Money(Decimal("8000000000"), currency),
                debt_equity=Decimal("0.40"),
                dividend_history_years=15,
            ),
            Decimal("3.20"),  # per-share dividend (annual)
        ),
        (
            _stock("BNP", "BNP.PA", "PA", "FR0000131104", "financials", "FR"),
            Fundamentals(
                yield_=Decimal("0.060"),
                payout_ratio=Decimal("0.50"),
                free_cash_flow=Money(Decimal("5000000000"), currency),
                debt_equity=Decimal("1.20"),
                dividend_history_years=20,
            ),
            Decimal("4.40"),
        ),
        (
            _stock("SAN", "SAN.PA", "PA", "FR0000120578", "healthcare", "FR"),
            Fundamentals(
                yield_=Decimal("0.038"),
                payout_ratio=Decimal("0.65"),
                free_cash_flow=Money(Decimal("7000000000"), currency),
                debt_equity=Decimal("0.50"),
                dividend_history_years=25,
            ),
            Decimal("3.50"),
        ),
    ]
    out: list[Stock] = []
    for stock, fundamentals, dividend in seed_universe:
        data.register_fundamentals(stock.id, fundamentals)
        data.register_dividend(
            Dividend(
                instrument=stock.id,
                ex_date=datetime(2026, 5, 15, tzinfo=UTC),
                pay_date=datetime(2026, 5, 15, tzinfo=UTC),
                amount_gross=Money(dividend, currency),
            )
        )
        out.append(stock)
    return out


def _stock(  # noqa: PLR0913 — mirrors Stock fields
    symbol: str, iid: str, exchange: str, isin: str, sector: str, country: str
) -> Stock:
    return Stock(
        id=InstrumentId(iid),
        symbol=symbol,
        exchange=exchange,
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=isin,
        sector=sector,
        country=country,
    )


# ----------------------------------------------------------------------
# Stub safety layer for the demo (the live runtime injects the real
# StateManager; here we keep the kill switch ACTIVE so trades flow).
# ----------------------------------------------------------------------


class _DemoSafety:
    def must_halt(self) -> bool:
        return False

    def state(self) -> KillSwitchState:
        return KillSwitchState.ACTIVE

    def raise_trigger(self, trigger: KillSwitchTrigger) -> None:
        # Demo prints; production wires this into the audit log.
        print(f"[safety] trigger raised: {trigger.code} ({trigger.message})", file=sys.stderr)


# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------


def run(  # noqa: PLR0913 — orchestration entry; one arg per pipeline stage's input
    *,
    config_dir: Path = DEFAULT_CONFIG_DIR,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    timeframe: Timeframe = Timeframe.D1,
    use_slippage: bool = False,
    out_stream: Any = sys.stdout,
) -> Result[DashboardView, str]:
    """Run the full pipeline once; return the dashboard view on
    success or a categorised Err."""
    # 1. Configuration (REQ_O_003).
    sys_res = _load_system_config(config_dir / "system.yaml")
    if isinstance(sys_res, Err):
        return Err(sys_res.error)
    sys_cfg = sys_res.value

    phase_res = load_phase_engine(config_dir / "phases.yaml")
    if isinstance(phase_res, Err):
        return Err(f"main:phase_engine:{phase_res.error}")
    phase_engine = phase_res.value

    risk_res = load_risk_config(config_dir / "risk.yaml")
    if isinstance(risk_res, Err):
        return Err(f"main:risk_config:{risk_res.error}")
    risk_cfg = risk_res.value

    # tax.yaml load follows same pattern; default is fine for the demo
    tax_cfg = TaxConfig.default()

    # 2. Data + broker.
    data = MockMarketDataProvider(seed=sys_cfg.seed)
    universe = _build_universe(data, sys_cfg.starting_capital.currency)

    # 3. Phase resolution: derive PhaseConstraints from the loaded
    #    PhaseEngine + the starting capital.
    phase = phase_engine.resolve(sys_cfg.starting_capital)
    pc = phase_engine.constraints_for(phase)

    # 4. Screener (REQ_F_SCR_001).
    screened = screen(universe, data, ScreenerConfig())
    print(f"[screener] {len(screened)} stocks survived the filter", file=out_stream)

    # 5. Strategy (REQ_F_STR_001).
    fee_model = FlatFeeModel(commission=_eur_zero(sys_cfg), spread_bps=Decimal("5"))
    slip_model = (
        GaussianSlippageModel(stdev_pct=Decimal("0.0005")) if use_slippage else ZeroSlippageModel()
    )
    strategy = CoreStrategy(cfg=CoreStrategyConfig(), fee_model=fee_model, tax_cfg=tax_cfg)
    risk_engine = RiskEngine(cfg=risk_cfg, safety=_DemoSafety())

    # 6. Backtest assembly + run (REQ_F_BCT_001..009).
    cfg = BacktestConfig(
        seed=sys_cfg.seed,
        start=start,
        end=end,
        timeframe=timeframe,
        starting_capital=sys_cfg.starting_capital,
        tax=tax_cfg,
    )
    assemble_res = Backtest.assemble(
        cfg=cfg,
        strategies=(strategy,),
        strategy_buckets={strategy.id: AllocationBucket.STOCK},
        instruments=tuple(universe),
        data=data,
        fee_model=fee_model,
        slippage_model=slip_model,
        risk=risk_engine,
        pc=pc,
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=tuple(screened),
    )
    if isinstance(assemble_res, Err):
        return Err(f"main:backtest_assemble:{assemble_res.error}")
    backtest = assemble_res.value
    result = backtest.run()

    # 7. Dashboard (REQ_F_DSH_001 / REQ_SDS_MOD_015).
    analytics = Analytics(
        portfolio=backtest.portfolio,
        capital_flow=backtest.capflow,
        trades=result.trades,
    )
    dashboard = Dashboard(
        analytics=analytics,
        phase=Phase(phase.value),
        orders=dict.fromkeys((t.order_id for t in result.trades), strategy.id),
    )
    view = dashboard.render(end)

    _print_summary(view, sys_cfg, result, out_stream)
    return Ok(view)


def _eur_zero(sys_cfg: _SystemConfig) -> Money:
    return Money(Decimal(0), sys_cfg.starting_capital.currency)


def _print_summary(
    view: DashboardView,
    sys_cfg: _SystemConfig,
    result: Any,
    out_stream: Any,
) -> None:
    """Print the headline numbers a Phase-1 operator wants to see."""
    perf = view.performance
    cap = sys_cfg.starting_capital
    rg, rn = perf.realized_gross, perf.realized_after_tax
    dg, dn = perf.dividends_gross, perf.dividends_after_tax
    print("", file=out_stream)
    print("=" * 72, file=out_stream)
    print(f"trading-bot demo run — phase {view.phase.value}", file=out_stream)
    print("=" * 72, file=out_stream)
    print(f"  starting capital     {cap.amount} {cap.currency.value}", file=out_stream)
    print(f"  trades               {perf.trade_count}", file=out_stream)
    print(f"  realized gross       {rg.amount} {rg.currency.value}", file=out_stream)
    print(f"  realized after tax   {rn.amount} {rn.currency.value}", file=out_stream)
    print(f"  dividends gross      {dg.amount} {dg.currency.value}", file=out_stream)
    print(f"  dividends after tax  {dn.amount} {dn.currency.value}", file=out_stream)
    print(
        f"  fees total           {perf.fees_total.amount} {perf.fees_total.currency.value}",
        file=out_stream,
    )
    print(f"  total return (net)   {perf.total_return_after_tax_pct}", file=out_stream)
    print(f"  max drawdown         {perf.max_drawdown_pct}", file=out_stream)
    print(f"  sharpe (after tax)   {perf.sharpe_after_tax}", file=out_stream)
    print("", file=out_stream)
    print("  allocation by class:", file=out_stream)
    for row in view.allocation:
        print(f"    {row.instrument_class.value:<14} {row.exposure_pct}", file=out_stream)
    print("", file=out_stream)
    print(f"  equity curve points  {len(result.equity_curve)}", file=out_stream)
    print(f"  trade history rows   {len(view.trade_history)}", file=out_stream)
    print("=" * 72, file=out_stream)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help="Directory holding system.yaml / phases.yaml / risk.yaml etc.",
    )
    parser.add_argument(
        "--start",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC),
        default=DEFAULT_START,
        help="Backtest start (ISO-8601).",
    )
    parser.add_argument(
        "--end",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC),
        default=DEFAULT_END,
        help="Backtest end (ISO-8601).",
    )
    parser.add_argument(
        "--with-slippage",
        action="store_true",
        help="Apply seeded Gaussian slippage on every fill.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    res = run(
        config_dir=args.config_dir,
        start=args.start,
        end=args.end,
        use_slippage=args.with_slippage,
    )
    match res:
        case Ok(_):
            return 0
        case Err(reason):
            print(f"main: ERROR {reason}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
