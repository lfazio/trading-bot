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
import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_system.accounts.factory import AccountComponents, build_default_registry
from trading_system.accounts.group import PortfolioGroup
from trading_system.accounts.household_drawdown_trigger import HouseholdDrawdownTrigger
from trading_system.accounts.registry import AccountRegistry
from trading_system.analytics import Analytics
from trading_system.backtesting import Backtest, BacktestConfig
from trading_system.backtesting.result import BacktestResult
from trading_system.config import SystemConfig, load_system_config, validate_all
from trading_system.dashboard import Dashboard, DashboardView
from trading_system.data.fundamentals.composite import CompositeFundamentalsProvider
from trading_system.data.fundamentals.csv_provider import CSVFundamentalsProvider
from trading_system.data.fundamentals.config import FundamentalsConfig
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Fundamentals, Timeframe
from trading_system.data.universes import Universe, load_universe
from trading_system.data.yfinance.bundled import (
    populate_cache_from_bundled_fixtures,
)
from trading_system.data.yfinance.cache import YFinanceCache
from trading_system.data.yfinance.provider import YFinanceMarketDataProvider
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
class RunOutcome:
    """Bundle produced by a single ``run()`` invocation.

    CR-016 Phase B — the CLI's ``backtest`` handler consumes this
    to emit the 5-file report directory via
    ``trading_system.analytics.write_report``. ``view`` keeps the
    pre-Phase-B return shape so callers that only read the
    dashboard stay one-line migrations away.

    CR-006 Phase B — ``registry`` carries the runtime's
    :class:`AccountRegistry` (single ``default`` account on the
    legacy path per REQ_NF_ACC_001) so downstream consumers
    (HouseholdDrawdownTrigger, the future webapp's
    LiveStateReader, persistence call sites) can look up the
    active account-bound cursors instead of re-deriving them.
    ``household_drawdown`` is the final household-level drawdown
    evaluated after the backtest completes; ``None`` when no
    breach.
    """

    view: DashboardView
    result: BacktestResult
    config_hash: str
    seed: int
    data_provider: str
    registry: AccountRegistry | None = None
    household_drawdown_trip: str | None = None


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


def _build_data_provider(
    sys_cfg: SystemConfig,
    *,
    config_dir: Path,
) -> Result[object, str]:
    """Construct the MarketDataProvider selected by ``sys_cfg.data``.

    - ``provider: mock``     ⇒ legacy ``MockMarketDataProvider``
      seeded from ``system.yaml``'s ``seed`` field (zero network,
      synthetic universe; backwards-compat default).
    - ``provider: yfinance`` ⇒ ``YFinanceMarketDataProvider`` over
      a ``YFinanceCache`` at ``data.cache_root``. When
      ``data.bundled_fixtures`` is True + the cache is empty, the
      shipped fixtures under ``data/yfinance-fixtures/`` are
      copied in so the demo runs without network. The yfinance
      provider is chained behind a ``CompositeFundamentalsProvider``
      with ``CSVFundamentalsProvider`` so the screener still gets
      fundamentals (yfinance.fundamentals is unsupported per
      REQ_F_DAT_010).
    """
    if sys_cfg.data.provider == "mock":
        return Ok(MockMarketDataProvider(seed=sys_cfg.seed))
    if sys_cfg.data.provider != "yfinance":
        return Err(f"data:unknown_provider:{sys_cfg.data.provider}")

    cache_root = Path(sys_cfg.data.cache_root)
    if sys_cfg.data.bundled_fixtures and (
        not cache_root.exists() or not any(cache_root.rglob("*.jsonl"))
    ):
        populate = populate_cache_from_bundled_fixtures(cache_root=cache_root)
        if isinstance(populate, Err):
            return Err(populate.error)

    cache = YFinanceCache(root=cache_root)
    yfinance_provider = YFinanceMarketDataProvider(
        cache=cache,
        currency=sys_cfg.starting_capital.currency,
        allow_network=False,
    )

    # The CSV fundamentals provider lands behind the yfinance one so
    # CR-014's seeded fundamentals fill in for the yfinance provider's
    # unsupported ``fundamentals(...)`` method.
    csv_path = config_dir.parent / "data" / "seed_fundamentals.csv"
    if csv_path.exists():
        csv_provider = CSVFundamentalsProvider(
            FundamentalsConfig(csv_path=csv_path)
        )
        return Ok(
            CompositeFundamentalsProvider(
                delegates=(yfinance_provider, csv_provider)
            )
        )
    return Ok(yfinance_provider)


def _build_runtime_universe(sys_cfg: SystemConfig, data: object) -> list[Stock]:
    """Resolve the universe of stocks the runtime backtest consumes.

    - ``data.universe`` set ⇒ load the named preset
      (``data/universes/<name>.yaml``).
    - else + mock provider ⇒ legacy hand-built 3-stock universe
      with mock fundamentals + dividends registered on the
      provider (REQ_NF_ACC_001 backwards compat).
    - else + yfinance provider ⇒ load ``eu-dividend-starter``
      preset (aligned with the bundled fixtures).
    """
    name = sys_cfg.data.universe.strip()
    if name:
        uni_result = load_universe(name)
        if isinstance(uni_result, Ok):
            return list(uni_result.value.stocks)
        # Fall through to the default path on lookup failure so the
        # demo stays usable; the structured logger surfaces the
        # error.
    if sys_cfg.data.provider == "yfinance":
        starter = load_universe("eu-dividend-starter")
        if isinstance(starter, Ok):
            return list(starter.value.stocks)
    # Legacy mock path: hand-build with registered mock fundamentals.
    if isinstance(data, MockMarketDataProvider):
        return _build_universe(data, sys_cfg.starting_capital.currency)
    # Last-resort fallback: empty universe (screener returns []).
    return []


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
) -> Result[RunOutcome, str]:
    """Run the full pipeline once; return the dashboard view + the
    raw BacktestResult on success, or a categorised Err.

    The returned ``RunOutcome`` carries everything the CR-016 MVP-4
    ``write_report`` surface needs (config_hash + seed + data
    provider label) so the CLI can emit the 5-file report directory
    without a second pass over the configuration.
    """
    # 1. Configuration (REQ_O_003).
    sys_res = load_system_config(config_dir / "system.yaml")
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

    # 2. Data + broker. CR-016 MVP-2 — provider selection driven by
    #    ``system.yaml``'s ``data:`` section.
    data_result = _build_data_provider(sys_cfg, config_dir=config_dir)
    if isinstance(data_result, Err):
        return Err(f"main:data_provider:{data_result.error}")
    data = data_result.value
    universe = _build_runtime_universe(sys_cfg, data)

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

    # CR-006 Phase B — build the AccountRegistry alongside the
    # backtest. v1 ships the legacy single-account default per
    # REQ_NF_ACC_001 backwards-compat; multi-account deployments
    # populate via accounts.yaml (CR-006 Phase B follow-up that lives
    # outside the demo path). The registry references the SAME
    # portfolio / capflow cursors the backtest engine mutates, so
    # PortfolioGroup queries see live state without a separate
    # mark-up step.
    registry_res = build_default_registry(
        config_dir=config_dir,
        components=AccountComponents(
            broker=backtest.broker,
            portfolio=backtest.portfolio,
            capital_flow=backtest.capflow,
            phase_engine=phase_engine,
            risk_overlay=None,  # operator overlays land via accounts.yaml
        ),
    )
    if isinstance(registry_res, Err):
        return Err(f"main:account_registry:{registry_res.error}")
    registry = registry_res.value

    result = backtest.run()

    # CR-006 Phase B — evaluate the household drawdown trigger once
    # the backtest finishes so operators see the final-state breach
    # in the RunOutcome. v1 is a single-account deployment so the
    # household drawdown equals the per-account drawdown
    # (PortfolioGroup.household_drawdown returns the max across
    # accounts).
    drawdown_trip = _evaluate_household_drawdown(registry, at=end)

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
    return Ok(
        RunOutcome(
            view=view,
            result=result,
            config_hash=_config_hash(sys_cfg, start, end, timeframe, use_slippage),
            seed=sys_cfg.seed,
            data_provider=sys_cfg.data.provider,
            registry=registry,
            household_drawdown_trip=drawdown_trip,
        )
    )


def _evaluate_household_drawdown(
    registry: AccountRegistry,
    *,
    at: datetime,
) -> str | None:
    """CR-006 Phase B — read the household drawdown via
    PortfolioGroup + HouseholdDrawdownTrigger and return a
    severity string (``"DEGRADE"`` / ``"KILL"``) if the threshold
    fires, else ``None``.

    The Portfolio cursor at this point holds the backtest's
    final state — the deterministic engine never mutates it
    after ``run()`` returns. v1 returns severity as a string;
    Phase-6 wires the actual ``SafetyLayer.raise_trigger`` call
    so the kill-switch state-machine reacts.
    """
    from trading_system.models.identifiers import SnapshotId

    try:
        group = PortfolioGroup(registry=registry)
        trigger = HouseholdDrawdownTrigger(group=group)
        outcome = trigger.evaluate(
            at=at,
            snapshot_id=SnapshotId("main:final-state"),
        )
    except Exception:  # noqa: BLE001 — single-account demo path; phase-6 wires structured paths
        return None
    if isinstance(outcome, Err):
        return None
    triggered = outcome.value
    if triggered.is_none():
        return None
    return triggered.unwrap().severity


def _config_hash(
    sys_cfg: SystemConfig,
    start: datetime,
    end: datetime,
    timeframe: Timeframe,
    use_slippage: bool,
) -> str:
    """Deterministic SHA-256 over the inputs that fully determine
    the backtest outcome. The manifest's ``config_hash`` matches the
    CR-008 ``BacktestResultRepository`` replay-tuple convention so
    two reports built from the same configuration bytes share a
    config_hash."""
    payload = "|".join(
        (
            str(sys_cfg.starting_capital.amount),
            sys_cfg.starting_capital.currency.value,
            str(sys_cfg.seed),
            sys_cfg.mode,
            sys_cfg.broker_adapter,
            sys_cfg.data.provider,
            sys_cfg.data.cache_root,
            str(sys_cfg.data.bundled_fixtures),
            sys_cfg.data.universe,
            start.isoformat(),
            end.isoformat(),
            timeframe.value,
            str(use_slippage),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _eur_zero(sys_cfg: SystemConfig) -> Money:
    return Money(Decimal(0), sys_cfg.starting_capital.currency)


def _print_summary(
    view: DashboardView,
    sys_cfg: SystemConfig,
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
    # Structured logging — REQ_NF_LOG_001 / REQ_SDS_CRS_001. Absent
    # config/logging.yaml ⇒ defaults (INFO / json / stderr).
    from trading_system.observability import (
        configure_logging,
        load_logging_config,
    )

    logging_cfg_path = Path(args.config_dir) / "logging.yaml"
    if logging_cfg_path.exists():
        match load_logging_config(logging_cfg_path):
            case Err(reason):
                print(f"main: ERROR {reason}", file=sys.stderr)
                return 1
            case Ok(cfg):
                configure_logging(level=cfg.level, json_output=cfg.format == "json")
    else:
        configure_logging()  # defaults

    # Centralised startup config validation — REQ_SDS_CFG_001.
    # Every shipped YAML SHALL parse cleanly before the runtime starts;
    # aggregated Errs are printed so the operator fixes them in one cycle.
    match validate_all(args.config_dir):
        case Err(report):
            for line in report.errors:
                print(line, file=sys.stderr)
            print(
                f"main: ERROR config: {len(report.errors)} validation error(s)",
                file=sys.stderr,
            )
            return 1
        case Ok(_):
            pass

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
