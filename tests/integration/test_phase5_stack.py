"""Phase-5 integration drill — end-to-end composition test.

Every Phase-5 CR shipped as a v1 slice with broader runtime wiring
deferred. This test proves the **shipped composition** holds:

  - CR-014 ``CSVFundamentalsProvider`` + ``MockMarketDataProvider``
    chained via ``CompositeFundamentalsProvider``; the screener
    consumes fundamentals through the existing
    ``MarketDataProvider.fundamentals(...)`` Protocol.
  - CR-013 ``RegimeDetector`` classifies a synthetic bar series;
    ``TransitionTracker`` carries the cursor — proves the public
    API is end-to-end callable.
  - CR-011 ``compute_fx_exposure`` + ``FXHedger.propose_hedges`` over
    a multi-currency portfolio snapshot; ``FXHedgeLedger`` round-trips
    open/close with realised P&L; proves the v1 hedger algorithmically
    composes.
  - CR-015 ``TradeRationale`` rows attached to a synthetic
    ``BacktestResult``; ``analytics.rationale_for`` looks them up by
    ``trade_id``.
  - CR-008 ``BacktestResultRepository.archive`` + ``lookup`` round-trips
    the result **with rationales preserved bit-identically** — proves
    CR-014/015 don't break the CR-008 persistence path and that the
    persistence layer is the canonical storage for Phase-5 backtest
    artifacts.

A failure here means a wiring regression across modules — the kind
of break that would not show up in any per-CR unit suite. This is
the cheapest insurance before opening Phase-6 implementation work.

REQ refs: REQ_F_FND_001, REQ_F_FND_004, REQ_F_FND_005, REQ_F_RGM_001,
REQ_F_FXH_001, REQ_F_FXH_002, REQ_F_FXH_003, REQ_F_FXH_005,
REQ_F_RAT_001, REQ_F_RAT_004, REQ_F_PER_002, REQ_F_PER_007,
REQ_NF_PER_001.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_system.analytics import rationale_for
from trading_system.backtesting.result import BacktestResult
from trading_system.data.fundamentals import (
    CompositeFundamentalsProvider,
    CSVFundamentalsProvider,
    FundamentalsConfig,
)
from trading_system.data.mock import MockMarketDataProvider
from trading_system.data.types import Bar
from trading_system.data.types import Fundamentals as FundamentalsType
from trading_system.models.flow import EquityPoint
from trading_system.models.identifiers import (
    InstrumentId,
    OrderId,
    StrategyId,
    TradeId,
)
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency, Money
from trading_system.models.phase import MarketRegime
from trading_system.models.rationale import TradeRationale, validate_gate_vocabulary
from trading_system.models.trading import Trade
from trading_system.persistence.connection import Connection
from trading_system.persistence.migrations.runner import MigrationRunner
from trading_system.persistence.repositories.backtest import (
    BacktestResultRepository,
)
from trading_system.regime.config import RegimeConfig
from trading_system.regime.detector import RegimeDetector
from trading_system.regime.transition import TransitionTracker
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.screener import ScreenerConfig, screen
from trading_system.wealth_ops.fx_hedger import (
    FXHedgeLedger,
    FXHedger,
    HedgePolicy,
    MarkedPosition,
    compute_fx_exposure,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_MIGRATIONS = _REPO_ROOT / "trading_system" / "persistence" / "migrations"


# ---------------------------------------------------------------------------
# Universe + fixtures
# ---------------------------------------------------------------------------


def _eu_stock(symbol: str, iid: str, sector: str) -> Stock:
    return Stock(
        id=InstrumentId(iid),
        symbol=symbol,
        exchange=iid.split(".")[-1],
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"{iid.replace('.', '')}-ISIN",
        sector=sector,
        country=iid.split(".")[-1],
    )


_HEADER = (
    "instrument_id,yield_,payout_ratio,free_cash_flow_amount,"
    "free_cash_flow_currency,debt_equity,dividend_history_years,as_of_date"
)


def _seed_csv(path: Path) -> Path:
    body = (
        f"{_HEADER}\n"
        # ASML / BNP / SAN — same instrument ids as main.py demo so a
        # screener run produces a deterministic ranking.
        "ASML.AS,0.045,0.55,8000000000,EUR,0.40,15,2026-04-01\n"
        "BNP.PA,0.060,0.50,5000000000,EUR,1.20,20,2026-04-01\n"
        "SAN.PA,0.038,0.65,7000000000,EUR,0.50,25,2026-04-01\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The integration drill
# ---------------------------------------------------------------------------


def test_phase5_end_to_end_composition(tmp_path: Path) -> None:
    """One test, one assertion-rich pipeline. Breaks loudly if any
    Phase-5 CR's public surface drifts.

    Reads as a single linear story: build the data layer (mock + CSV
    fundamentals), run the screener through the composite, classify a
    regime, compute FX exposure + propose hedges, assemble a synthetic
    BacktestResult with TradeRationale rows, archive through the
    persistence layer, and assert the round-trip is bit-identical.
    """
    # -------------------------------------------------------------------
    # 1. CR-014 — CompositeFundamentalsProvider over CSV + Mock.
    # -------------------------------------------------------------------
    csv_path = _seed_csv(tmp_path / "fundamentals.csv")
    csv_provider = CSVFundamentalsProvider(
        config=FundamentalsConfig(csv_path=csv_path, max_age_days=547),
        _today=lambda: date(2026, 5, 15),
    )
    mock_provider = MockMarketDataProvider(seed=42)
    # Order matters: the CSV is the fundamentals authority; the mock
    # provider serves bars / dividends / latest. The composite tries
    # delegates in order — first Ok wins, last Err loses.
    composite = CompositeFundamentalsProvider(
        delegates=(csv_provider, mock_provider)
    )
    universe = [
        _eu_stock("ASML", "ASML.AS", "tech"),
        _eu_stock("BNP", "BNP.PA", "financials"),
        _eu_stock("SAN", "SAN.PA", "healthcare"),
    ]

    # The screener consumes the composite via the existing
    # MarketDataProvider Protocol — no special wiring.
    scored = screen(universe, composite, ScreenerConfig())
    # At least one of the three should clear the default screen.
    assert len(scored) >= 1, (
        "CR-014 wiring regression: screener got zero fundamentals from "
        "CompositeFundamentalsProvider(CSV + Mock). Either CSV rows "
        "fail the default ScreenerConfig filter, or the composite isn't "
        "delegating fundamentals() correctly."
    )
    # Spot-check: ASML's fundamentals SHALL come from the CSV (the mock
    # provider has no fundamentals registered for it — would return Err).
    asml_res = composite.fundamentals(_eu_stock("ASML", "ASML.AS", "tech"))
    match asml_res:
        case Ok(f):
            assert isinstance(f, FundamentalsType)
            assert f.yield_ == Decimal("0.045")  # exactly the CSV value
            assert f.free_cash_flow.currency is Currency.EUR
        case Err(reason):
            raise AssertionError(
                f"CR-014: CSV provider didn't serve ASML.AS — {reason}"
            )

    # -------------------------------------------------------------------
    # 2. CR-013 — RegimeDetector classifies a synthetic BULL bar
    #    series; TransitionTracker carries the cursor.
    # -------------------------------------------------------------------
    regime_cfg = RegimeConfig(
        ma_short=10, ma_long=30, vol_window=10, confirmation_periods=2
    )
    detector = RegimeDetector(config=regime_cfg)
    bars = _synthetic_uptrend()
    detected = detector.evaluate(bars).unwrap()
    assert detected is MarketRegime.BULL, (
        "CR-013 wiring regression: synthetic uptrend didn't classify "
        f"as BULL (got {detected})"
    )

    tracker = TransitionTracker(confirmation_periods=2)
    # First observation seeds the cursor; no event emitted.
    first = tracker.observe(detected, at=datetime(2026, 5, 15, 9, 0, tzinfo=UTC))
    assert isinstance(first, Nothing)

    # -------------------------------------------------------------------
    # 3. CR-011 — compute FX exposure + propose hedges over a
    #    multi-currency portfolio snapshot; ledger round-trips.
    # -------------------------------------------------------------------
    positions = [
        # EUR positions filtered out (base currency).
        MarkedPosition(
            currency=Currency.EUR,
            value_in_base=Money(Decimal("50000"), Currency.EUR),
        ),
        MarkedPosition(
            currency=Currency.USD,
            value_in_base=Money(Decimal("20000"), Currency.EUR),
        ),
        MarkedPosition(
            currency=Currency.GBP,
            value_in_base=Money(Decimal("3000"), Currency.EUR),  # 3% — below
        ),
    ]
    household = Money(Decimal("100000"), Currency.EUR)
    exposure = compute_fx_exposure(
        positions, base_currency=Currency.EUR, household_equity=household
    )
    # USD share = 20000/100000 = 0.20; GBP = 0.03.
    assert exposure[Currency.USD] == Decimal("0.2")
    assert exposure[Currency.GBP] == Decimal("0.03")
    assert Currency.EUR not in exposure  # filtered out

    hedger = FXHedger(
        policy=HedgePolicy(
            threshold_pct=Decimal("0.05"),
            target_hedge_ratio=Decimal("0.80"),
        )
    )
    proposals = hedger.propose_hedges(
        exposures=exposure,
        household_equity=household,
        base_currency=Currency.EUR,
        now=datetime(2026, 5, 15, 9, 0, tzinfo=UTC),
    )
    # GBP is below the 5% threshold — only USD produces a proposal.
    assert len(proposals) == 1
    assert proposals[0].currency is Currency.USD

    # Open + close one forward through the ledger and verify P&L.
    ledger = FXHedgeLedger()
    forward = ledger.open(
        proposals[0],
        entry_fx_rate=Decimal("1.10"),
        opened_at=datetime(2026, 5, 15, 9, 30, tzinfo=UTC),
    )
    pnl_res = ledger.close(
        forward.id,
        exit_fx_rate=Decimal("1.05"),
        closed_at=datetime(2026, 6, 15, 9, 30, tzinfo=UTC),
    )
    assert isinstance(pnl_res, Ok)
    # Notional = 20000 × 0.80 = 16000; PnL = 16000 × (1.05/1.10 - 1).
    expected_pnl = Decimal("16000.00") * (
        Decimal("1.05") / Decimal("1.10") - Decimal(1)
    )
    assert pnl_res.value.amount == expected_pnl
    # After tax: losses pass through pre-tax (REQ_F_FXH_006).
    assert ledger.realized_pnl_after_tax() == ledger.realized_pnl_gross()

    # -------------------------------------------------------------------
    # 4. CR-015 — assemble a synthetic BacktestResult with rationales.
    # -------------------------------------------------------------------
    trades = (
        _trade("trade-1", day=8),
        _trade("trade-2", day=9),
    )
    rationales = (
        _rationale("trade-1", reason="ASML.AS yield 4.5% > 4.0% threshold; payout 55% < 70%"),
        _rationale("trade-2", reason="BNP.PA dividend history 20y; payout 50%"),
    )
    result = BacktestResult(
        trades=trades,
        equity_curve=(_point(8), _point(9)),
        equity_excl_injections=(Decimal("100000"), Decimal("100100")),
        final_cash=Money(Decimal("99700"), Currency.EUR),
        final_equity_after_tax=Money(Decimal("100100"), Currency.EUR),
        realized_gross=Money(Decimal("400"), Currency.EUR),
        realized_after_tax=Money(Decimal("280"), Currency.EUR),
        dividends_gross=Money(Decimal("10"), Currency.EUR),
        dividends_after_tax=Money(Decimal("7"), Currency.EUR),
        knockouts=0,
        injections_applied=0,
        rationales=rationales,
    )
    # The aligned-length invariant SHALL hold.
    assert len(result.rationales) == len(result.trades)

    # The public read surface — analytics.rationale_for — is the
    # canonical lookup (REQ_SDS_RAT_001).
    match rationale_for(result, TradeId("trade-1")):
        case Some(r):
            assert "ASML" in r.signal_reason
            # The gate-name vocabulary audit SHALL pass.
            assert isinstance(validate_gate_vocabulary(r), Ok)
        case _:
            raise AssertionError("CR-015: rationale_for(trade-1) returned Nothing")

    # -------------------------------------------------------------------
    # 5. CR-008 — round-trip the result with rationales through the
    #    persistence layer; assert bit-identical equality on read-back.
    # -------------------------------------------------------------------
    conn = Connection.open(tmp_path / "state.sqlite").unwrap()
    MigrationRunner(conn=conn, migrations_dir=_BUNDLED_MIGRATIONS).run().unwrap()
    repo = BacktestResultRepository(conn=conn)
    assert isinstance(
        repo.archive(
            result,
            strategy_id=StrategyId("integration-strat"),
            git_sha="integration-sha",
            config_hash="integration-cfg",
            seed=42,
        ),
        Ok,
    )
    loaded = repo.lookup(
        StrategyId("integration-strat"),
        "integration-sha",
        "integration-cfg",
        42,
    ).unwrap()
    # Bit-identical structural equality (REQ_NF_PER_001 / TC_PER_007).
    assert loaded == result, (
        "CR-008 round-trip regression: BacktestResult with rationales "
        "did NOT round-trip bit-identically. Likely cause: a recent "
        "field addition broke the persistence mapper."
    )
    assert loaded.rationales == rationales
    # The lookup-after-restart path also works through the analytics
    # helper (the loaded result should serve rationale_for identically).
    match rationale_for(loaded, TradeId("trade-2")):
        case Some(r):
            assert "BNP" in r.signal_reason
        case _:
            raise AssertionError(
                "CR-008 + CR-015 wiring: loaded result didn't serve "
                "rationale_for(trade-2)"
            )


# ---------------------------------------------------------------------------
# Helpers — synthetic data scoped to the integration test
# ---------------------------------------------------------------------------


def _synthetic_uptrend(n: int = 50) -> list[Bar]:
    """Smooth uptrend bar series — classifies as BULL under the
    default RegimeDetector config."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = [Decimal("100") + Decimal("0.4") * Decimal(i) for i in range(n)]
    return [
        Bar(
            at=start + timedelta(days=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1000"),
        )
        for i, close in enumerate(closes)
    ]


def _trade(trade_id: str, *, day: int) -> Trade:
    return Trade(
        id=TradeId(trade_id),
        order_id=OrderId(f"order-{trade_id}"),
        executed_at=datetime(2026, 5, day, 9, 30, tzinfo=UTC),
        price=Decimal("100.0"),
        quantity_filled=Decimal("10"),
        fees=Money(Decimal("0.50"), Currency.EUR),
    )


def _point(day: int) -> EquityPoint:
    return EquityPoint(
        at=datetime(2026, 5, day, tzinfo=UTC),
        equity_gross=Money(Decimal("100000"), Currency.EUR),
        equity_after_tax=Money(Decimal("100000"), Currency.EUR),
        drawdown_pct=Decimal("0.0"),
    )


def _rationale(trade_id: str, *, reason: str) -> TradeRationale:
    return TradeRationale(
        trade_id=TradeId(trade_id),
        strategy_id=StrategyId("integration-strat"),
        strategy_version="integration-sha",
        signal_reason=reason,
        risk_approval={
            "tax_gate": "verdict=pass",
            "risk_per_trade": "metric=0.012; threshold=0.015; verdict=pass",
            "stop_loss": "verdict=pass",
        },
        tax_gate_decision="expected_net 12 EUR > 5 × fees 1.5 = 7.5 EUR",
        improvement_report_id="",
        decided_at=datetime(2026, 5, 8, 9, 30, tzinfo=UTC),
    )
