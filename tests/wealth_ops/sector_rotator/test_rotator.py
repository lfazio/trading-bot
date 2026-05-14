"""Tests for ``trading_system.wealth_ops.sector_rotator.rotator``.

Covers TC_SCT_002..006 + TC_SCT_008.

REQ refs:
- REQ_F_SCT_001 — phase guard (rotator off below phase 5).
- REQ_F_SCT_002 — regime-to-sector-tilt table.
- REQ_F_SCT_003 — minimum holding period.
- REQ_F_SCT_004 — whipsaw guard.
- REQ_F_SCT_005 — sector taxonomy / unknown-sector reject.
- REQ_F_SCT_006 — rotation cap per quarter.
- REQ_F_SCT_007 — provenance on every emitted proposal.
- REQ_NF_SCT_001 — determinism.
- REQ_SDS_SCT_001 — Protocol-only public surface.
- REQ_SDS_SCT_002 — phase engine is the single activator.
- REQ_SDS_SCT_003 — single mutable cursor.
- REQ_SDD_SCT_002 — phase guard short-circuits without consulting inputs.
- REQ_SDD_SCT_005 — quarter rollover resets the rotation count.
- REQ_SDD_SCT_006 — regime-episode interval semantics.
- REQ_SDD_SCT_007 — flagged-exit holding-period gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import InstrumentClass, Stock
from trading_system.models.money import Currency
from trading_system.models.phase import MarketRegime, Phase
from trading_system.screener.engine import ScoreBreakdown, ScoredStock
from trading_system.wealth_ops.sector_rotator import (
    HoldingState,
    RegimeSectorBias,
    RotationPolicy,
    SectorRotator,
    SectorTaxonomy,
)

EUR = Currency.EUR


def _ts(year: int = 2026, month: int = 5, day: int = 8) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _stock(symbol: str, sector: str) -> Stock:
    return Stock(
        id=InstrumentId(f"{symbol}.AS"),
        symbol=symbol,
        exchange="AS",
        currency=EUR,
        cls=InstrumentClass.STOCK,
        isin=f"NL{symbol:0>10}",
        sector=sector,
        country="NL",
    )


def _scored(symbol: str, sector: str, score: str = "0.5") -> ScoredStock:
    breakdown = ScoreBreakdown(
        stability=Decimal("0.5"),
        yield_quality=Decimal("0.5"),
        valuation=Decimal("0.5"),
    )
    return ScoredStock(
        stock=_stock(symbol, sector),
        score=Decimal(score),
        breakdown=breakdown,
    )


def _bias_bull_sideways() -> RegimeSectorBias:
    return RegimeSectorBias(
        table={
            MarketRegime.BULL: {"tech": Decimal("0.7"), "financials": Decimal("0.3")},
            MarketRegime.SIDEWAYS: {"tech": Decimal("0.5"), "financials": Decimal("0.5")},
            MarketRegime.BEAR: {"tech": Decimal("0.2"), "financials": Decimal("0.8")},
        }
    )


def _taxonomy(*sectors: str) -> SectorTaxonomy:
    return SectorTaxonomy(allowed=frozenset(sectors or {"tech", "financials"}))


def _rotator(
    *,
    state: HoldingState | None = None,
    policy: RotationPolicy | None = None,
    bias: RegimeSectorBias | None = None,
    taxonomy: SectorTaxonomy | None = None,
) -> SectorRotator:
    return SectorRotator(
        bias=bias or _bias_bull_sideways(),
        taxonomy=taxonomy or _taxonomy(),
        policy=policy or RotationPolicy(),
        state=state or HoldingState(),
        policy_id="rotator-v1",
    )


def _ranking(*pairs: tuple[str, str]) -> tuple[ScoredStock, ...]:
    """``[(symbol, sector), ...]`` -> ranking tuple."""
    return tuple(_scored(symbol, sector) for symbol, sector in pairs)


# ---------------------------------------------------------------------------
# TC_SCT_002 — phase guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", [Phase.ONE, Phase.TWO, Phase.THREE, Phase.FOUR])
def test_below_phase_5_returns_empty(phase: Phase) -> None:
    rotator = _rotator()
    out = rotator.evaluate(
        phase=phase,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "tech"), ("C", "financials")),
        at=_ts(),
    )
    assert out == ()


def test_phase_5_admits_a_proposal() -> None:
    rotator = _rotator()
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "tech"), ("C", "financials")),
        at=_ts(),
    )
    assert len(out) == 1
    proposal = out[0]
    assert proposal.source_regime is MarketRegime.BULL
    assert proposal.dest_weights == {"tech": Decimal("0.7"), "financials": Decimal("0.3")}
    assert proposal.policy_id == "rotator-v1"


# ---------------------------------------------------------------------------
# TC_SCT_003 — holding-period gate
# ---------------------------------------------------------------------------


def test_holding_period_blocks_when_within_window() -> None:
    state = HoldingState()
    state.last_entry["financials"] = _ts(2026, 5, 1)  # entered 7 days ago
    rotator = _rotator(state=state, policy=RotationPolicy(min_holding_days=60))
    # BULL -> sectors tilt to tech 0.7 / financials 0.3; current
    # ranking has financials over-represented so the rotation flags
    # financials for exit. Held only 7 days < 60 -> drop.
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(
            ("A", "financials"),
            ("B", "financials"),
            ("C", "tech"),
        ),
        at=_ts(2026, 5, 8),
    )
    assert out == ()


def test_holding_period_passes_after_window_closes() -> None:
    state = HoldingState()
    state.last_entry["financials"] = _ts(2026, 1, 1)  # entered 4 months ago
    rotator = _rotator(state=state, policy=RotationPolicy(min_holding_days=60))
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(
            ("A", "financials"),
            ("B", "financials"),
            ("C", "tech"),
        ),
        at=_ts(2026, 5, 8),
    )
    assert len(out) == 1


# ---------------------------------------------------------------------------
# TC_SCT_004 — whipsaw dampener + regime reset
# ---------------------------------------------------------------------------


def test_whipsaw_dampener_blocks_second_change_in_same_episode() -> None:
    rotator = _rotator(policy=RotationPolicy(whipsaw_dampener=1))
    # First evaluate establishes the BULL episode.
    first = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 5, 8),
    )
    assert len(first) == 1
    # Second evaluate within the same regime episode would be the
    # 2nd direction-change (counter > dampener=1). Drop.
    second = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "tech"), ("C", "financials")),
        at=_ts(2026, 5, 9),
    )
    assert second == ()


def test_regime_change_resets_whipsaw_counter() -> None:
    # min_holding_days=0 so the holding-period gate doesn't shadow
    # the whipsaw / regime-reset behavior under test.
    rotator = _rotator(
        policy=RotationPolicy(
            whipsaw_dampener=1,
            max_rotations_per_quarter=10,
            min_holding_days=0,
        )
    )
    # Establish BULL episode + flip once.
    rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 5, 8),
    )
    rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "tech"), ("C", "financials")),
        at=_ts(2026, 5, 9),
    )
    # Crossing into BEAR resets the episode counter.
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BEAR,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 5, 10),
    )
    assert len(out) == 1
    assert out[0].source_regime is MarketRegime.BEAR


# ---------------------------------------------------------------------------
# TC_SCT_005 — unknown sector drops cycle
# ---------------------------------------------------------------------------


def test_unknown_sector_drops_cycle() -> None:
    # Taxonomy excludes "crypto"; ranking includes a crypto stock.
    rotator = _rotator(taxonomy=_taxonomy("tech", "financials"))
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "crypto")),
        at=_ts(),
    )
    assert out == ()


# ---------------------------------------------------------------------------
# TC_SCT_006 — quarter rotation cap + rollover
# ---------------------------------------------------------------------------


def test_quarter_cap_blocks_second_rotation() -> None:
    rotator = _rotator(policy=RotationPolicy(max_rotations_per_quarter=1))
    first = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 4, 1),
    )
    assert len(first) == 1
    # Different regime so whipsaw dampener doesn't fire; same
    # quarter so rotation cap blocks.
    second = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 5, 1),
    )
    assert second == ()


def test_quarter_rollover_resets_count() -> None:
    rotator = _rotator(policy=RotationPolicy(max_rotations_per_quarter=1, whipsaw_dampener=10))
    rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 4, 1),
    )
    # Cross into Q3 (July). Counter resets.
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.SIDEWAYS,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(2026, 7, 5),
    )
    assert len(out) == 1


# ---------------------------------------------------------------------------
# TC_SCT_008 — determinism
# ---------------------------------------------------------------------------


def test_identical_inputs_yield_identical_proposals() -> None:
    args = dict(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=_ranking(("A", "tech"), ("B", "financials")),
        at=_ts(),
    )
    r1 = _rotator()
    r2 = _rotator()
    out1 = r1.evaluate(**args)
    out2 = r2.evaluate(**args)
    assert out1 == out2


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_empty_policy_id_rejected() -> None:
    with pytest.raises(ValueError, match="policy_id"):
        SectorRotator(
            bias=_bias_bull_sideways(),
            taxonomy=_taxonomy(),
            policy=RotationPolicy(),
            state=HoldingState(),
            policy_id="",
        )


# ---------------------------------------------------------------------------
# Misc edge cases
# ---------------------------------------------------------------------------


def test_unknown_regime_in_bias_drops_silently() -> None:
    rotator = SectorRotator(
        bias=RegimeSectorBias(table={MarketRegime.BULL: {"tech": Decimal("1.0")}}),
        taxonomy=_taxonomy("tech"),
        policy=RotationPolicy(),
        state=HoldingState(),
        policy_id="rotator-v1",
    )
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.HIGH_VOL,
        screener_ranking=_ranking(("A", "tech")),
        at=_ts(),
    )
    assert out == ()


def test_empty_screener_ranking_returns_empty() -> None:
    rotator = _rotator()
    out = rotator.evaluate(
        phase=Phase.FIVE,
        regime=MarketRegime.BULL,
        screener_ranking=(),
        at=_ts(),
    )
    assert out == ()
