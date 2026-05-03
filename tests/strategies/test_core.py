"""Tests for ``trading_system.strategies.core``.

Verifies REQ_F_STR_001 (long-term / dividend / low-turnover behavior),
REQ_C_BHV_002 (rebalance band gates overtrading), REQ_SDD_API_005
(stable strategy id), and REQ_F_TAX_003 (TradeProposal carries
expected_net_profit & expected_fees).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_system.data.types import Bar
from trading_system.models.identifiers import InstrumentId, StrategyId
from trading_system.models.money import Currency, Money
from trading_system.models.phase import AllocationBucket
from trading_system.models.trading import (
    Position,
    Side,
    StopLoss,
)
from trading_system.strategies.core import CoreStrategy, CoreStrategyConfig
from trading_system.tax.config import TaxConfig

from .conftest import (
    StubMarketProvider,
    StubPortfolioView,
    make_fee_model,
    make_phase_constraints,
    make_scored_stock,
    make_state,
    make_stock,
)

EUR = Currency.EUR


def latest_bar(price: str = "100.05") -> Bar:
    p = Decimal(price)
    return Bar(
        at=datetime(2026, 5, 1),
        open=p,
        high=p,
        low=p,
        close=p,
        volume=Decimal(1000),
    )


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestCoreStrategyConfig:
    def test_defaults_valid(self) -> None:
        cfg = CoreStrategyConfig()
        assert cfg.rebalance_band == Decimal("0.02")
        assert cfg.expected_return_pct == Decimal("0.06")

    @pytest.mark.parametrize(
        "kwargs, msg",
        [
            ({"rebalance_band": Decimal("-0.01")}, "rebalance_band"),
            ({"tick_budget_pct": Decimal("1.5")}, "tick_budget_pct"),
            ({"max_position_pct": Decimal("1.5")}, "max_position_pct"),
            ({"stop_loss_pct": Decimal("0")}, "stop_loss_pct"),
            ({"stop_loss_pct": Decimal("1")}, "stop_loss_pct"),
            ({"expected_return_pct": Decimal("-0.01")}, "expected_return_pct"),
        ],
    )
    def test_invalid_rejected(self, kwargs: dict[str, Decimal], msg: str) -> None:
        with pytest.raises(ValueError, match=msg):
            CoreStrategyConfig(**kwargs)


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


class TestCoreEvaluate:
    def _strategy(self) -> CoreStrategy:
        return CoreStrategy(CoreStrategyConfig(), make_fee_model(), TaxConfig.default())

    def test_id_is_stable(self) -> None:
        # REQ_SDD_API_005
        assert self._strategy().id == StrategyId("core_v1")

    def test_no_proposals_when_at_target(self) -> None:
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.70")}),
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_no_proposals_below_rebalance_band(self) -> None:
        # Gap = 0.70 - 0.69 = 0.01 < default rebalance_band 0.02.
        # REQ_C_BHV_002.
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.69")}),
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_proposal_emitted_when_gap_is_wide(self) -> None:
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.40")}),
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        proposals = self._strategy().evaluate(state)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.side is Side.BUY
        assert p.size_pct_of_capital > 0
        assert p.size_pct_of_capital <= Decimal("0.10")  # tick_budget_pct cap
        assert p.stop_loss.price < latest_bar().close  # below entry

    def test_skips_already_held_stock(self) -> None:
        held = make_stock("HELD")
        new = make_stock("NEW")
        market = StubMarketProvider()
        market.latest_map[held.id] = latest_bar()
        market.latest_map[new.id] = latest_bar()
        position = Position(
            instrument=held,
            quantity=Decimal(10),
            avg_price=Decimal("100"),
            opened_at=datetime(2026, 1, 1),
            stop_loss=StopLoss(price=Decimal("80")),
        )
        state = make_state(
            portfolio=StubPortfolioView(
                exposures={AllocationBucket.STOCK: Decimal("0.40")},
                positions={held.id: position},
            ),
            screener_ranking=(
                make_scored_stock(stock=held, score="0.9"),
                make_scored_stock(stock=new, score="0.5"),
            ),
            market=market,
        )
        proposals = self._strategy().evaluate(state)
        assert len(proposals) == 1
        assert proposals[0].instrument.symbol == "NEW"

    def test_skips_when_market_data_missing(self) -> None:
        # No latest_map entry -> latest() returns Err -> stock skipped.
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.40")}),
            screener_ranking=(make_scored_stock(),),
        )
        assert self._strategy().evaluate(state) == []

    def test_budget_clamps_total_proposals(self) -> None:
        # tick_budget_pct=0.10, max_position_pct=0.10 — at most 1
        # full-size proposal regardless of how many candidates rank.
        market = StubMarketProvider()
        for sym in ("AAA", "BBB", "CCC", "DDD"):
            market.latest_map[InstrumentId(f"id-{sym}")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.30")}),
            screener_ranking=tuple(
                make_scored_stock(stock=make_stock(s, isin_suffix=s))
                for s in ("AAA", "BBB", "CCC", "DDD")
            ),
            market=market,
        )
        proposals = self._strategy().evaluate(state)
        total_size = sum(p.size_pct_of_capital for p in proposals)
        assert total_size <= Decimal("0.10")

    def test_returns_no_proposals_with_empty_universe(self) -> None:
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0")}),
            screener_ranking=(),
        )
        assert self._strategy().evaluate(state) == []

    def test_zero_equity_short_circuits(self) -> None:
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(
                equity_amount="0",
                cash_amount="0",
                exposures={AllocationBucket.STOCK: Decimal("0")},
            ),
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []

    def test_proposal_carries_estimates(self) -> None:
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(exposures={AllocationBucket.STOCK: Decimal("0.40")}),
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        proposals = self._strategy().evaluate(state)
        assert len(proposals) == 1
        p = proposals[0]
        # Both estimates carry the order's currency.
        assert p.expected_net_profit.currency is EUR
        assert p.expected_fees.currency is EUR
        # Net profit > 0 since expected_return_pct > 0.
        assert p.expected_net_profit.amount > Money(Decimal(0), EUR).amount
        # Fees > 0 since FlatFeeModel has a positive commission.
        assert p.expected_fees.amount > Decimal(0)

    def test_no_target_for_stock_bucket_yields_no_proposals(self) -> None:
        # Phase 1 actually has STOCK at 0.90; this test simulates a
        # constraint where STOCK is absent from allocation_targets
        # (current=0; gap=0; no proposal).
        constraints = make_phase_constraints(
            allocation_targets={
                AllocationBucket.TACTICAL: Decimal("1.0"),
            }
        )
        market = StubMarketProvider()
        market.latest_map[InstrumentId("id-ABC")] = latest_bar()
        state = make_state(
            portfolio=StubPortfolioView(),
            constraints=constraints,
            screener_ranking=(make_scored_stock(),),
            market=market,
        )
        assert self._strategy().evaluate(state) == []
