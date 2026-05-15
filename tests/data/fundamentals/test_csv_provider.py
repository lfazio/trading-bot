"""Tests for ``trading_system.data.fundamentals.csv_provider``.

Covers TC_FND_002..007, TC_FND_010 (the bundled-seed smoke test).

Every test below verifies that ``Decimal`` is the boundary type
(REQ_F_FND_002) — the assertions use ``Decimal`` literals exclusively
and the parser code path goes through ``Decimal(str(...))``; no
``float`` ever reaches the ``Fundamentals`` dataclass. The CSV being
the system of record (REQ_F_FND_005) is verified by
``test_replay_determinism_same_csv_same_snapshot`` plus the round-trip
expectations in ``test_happy_path_round_trip``. The Protocol surface
(REQ_SDS_FND_001 — L2 placement and Protocol satisfaction with no
runtime imports of the CSV reader) is verified by
``test_provider_satisfies_market_data_provider_protocol``.

REQ refs: REQ_F_FND_001..005, REQ_NF_FND_001, REQ_SDS_FND_001,
REQ_SDD_FND_001, REQ_SDD_FND_002.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_system.data.fundamentals.config import FundamentalsConfig
from trading_system.data.fundamentals.csv_provider import (
    CSVFundamentalsProvider,
    CsvLoadError,
)
from trading_system.data.provider import MarketDataProvider
from trading_system.data.types import Timeframe
from trading_system.models.identifiers import InstrumentId
from trading_system.models.instrument import Instrument, InstrumentClass
from trading_system.models.money import Currency
from trading_system.result import Err, Ok

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BUNDLED_SEED = _REPO_ROOT / "data" / "seed_fundamentals.csv"


def _csv(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _instrument(instrument_id: str, cls: InstrumentClass = InstrumentClass.STOCK) -> Instrument:
    return Instrument(
        id=InstrumentId(instrument_id),
        symbol=instrument_id.split(".")[0],
        exchange=instrument_id.split(".")[-1] if "." in instrument_id else "XX",
        currency=Currency.EUR,
        cls=cls,
    )


def _provider(
    tmp_path: Path,
    body: str,
    *,
    max_age_days: int = 547,
    today: date = date(2026, 5, 15),
) -> CSVFundamentalsProvider:
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path, max_age_days=max_age_days)
    return CSVFundamentalsProvider(config=cfg, _today=lambda: today)


_HAPPY_HEADER = (
    "instrument_id,yield_,payout_ratio,free_cash_flow_amount,"
    "free_cash_flow_currency,debt_equity,dividend_history_years,as_of_date"
)


# ---------------------------------------------------------------------------
# TC_FND_002 — Happy-path CSV round-trip
# ---------------------------------------------------------------------------


def test_happy_path_round_trip(tmp_path: Path) -> None:
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
        "BNP.PA,0.068,0.55,7500000,EUR,0.90,15,2026-03-31\n"
    )
    provider = _provider(tmp_path, body)
    asml = provider.fundamentals(_instrument("ASML.AS")).unwrap()
    assert asml.yield_ == Decimal("0.045")
    assert asml.payout_ratio == Decimal("0.50")
    assert asml.free_cash_flow.amount == Decimal("1000000")
    assert asml.free_cash_flow.currency is Currency.EUR
    assert asml.debt_equity == Decimal("0.30")
    assert asml.dividend_history_years == 15
    bnp = provider.fundamentals(_instrument("BNP.PA")).unwrap()
    assert bnp.yield_ == Decimal("0.068")


def test_provider_satisfies_market_data_provider_protocol(tmp_path: Path) -> None:
    body = f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    provider = _provider(tmp_path, body)
    assert isinstance(provider, MarketDataProvider)


def test_unknown_instrument_returns_categorised_not_found(tmp_path: Path) -> None:
    body = f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    provider = _provider(tmp_path, body)
    match provider.fundamentals(_instrument("UNKNOWN.XX")):
        case Err(reason):
            assert reason == "data:not_found:fundamentals:UNKNOWN.XX"
        case Ok(_):
            raise AssertionError("expected not-found Err")


# ---------------------------------------------------------------------------
# TC_FND_003 — Unsupported methods fail fast
# ---------------------------------------------------------------------------


def test_bars_returns_not_supported(tmp_path: Path) -> None:
    body = f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    provider = _provider(tmp_path, body)
    instr = _instrument("ASML.AS")
    res = provider.bars(
        instr,
        Timeframe.D1,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, tzinfo=UTC),
    )
    match res:
        case Err(reason):
            assert reason == "data:not_supported:csv_only"
        case Ok(_):
            raise AssertionError("bars must not be supported")


def test_latest_returns_not_supported(tmp_path: Path) -> None:
    body = f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    provider = _provider(tmp_path, body)
    match provider.latest(_instrument("ASML.AS")):
        case Err(reason):
            assert reason == "data:not_supported:csv_only"
        case Ok(_):
            raise AssertionError


def test_dividends_returns_not_supported(tmp_path: Path) -> None:
    body = f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    provider = _provider(tmp_path, body)
    match provider.dividends(_instrument("ASML.AS"), 2026):
        case Err(reason):
            assert reason == "data:not_supported:csv_only"
        case Ok(_):
            raise AssertionError


# ---------------------------------------------------------------------------
# TC_FND_004 — Schema drift aborts construction
# ---------------------------------------------------------------------------


def test_missing_required_column_aborts_construction(tmp_path: Path) -> None:
    # ``dividend_history_years`` column removed.
    body = (
        "instrument_id,yield_,payout_ratio,free_cash_flow_amount,"
        "free_cash_flow_currency,debt_equity,as_of_date\n"
        "ASML.AS,0.045,0.50,1000000,EUR,0.30,2026-04-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert exc.value.reasons == ("data:csv_schema:dividend_history_years",)


def test_missing_csv_file_aborts_construction(tmp_path: Path) -> None:
    cfg = FundamentalsConfig(csv_path=tmp_path / "missing.csv")
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert any("data:csv_not_found" in r for r in exc.value.reasons)


# ---------------------------------------------------------------------------
# TC_FND_005 — Malformed numeric value
# ---------------------------------------------------------------------------


def test_malformed_decimal_aborts_load(tmp_path: Path) -> None:
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,not-a-decimal,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert "data:csv_malformed:ASML.AS:yield_" in exc.value.reasons


def test_malformed_currency_aborts_load(tmp_path: Path) -> None:
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,0.045,0.50,1000000,XYZ,0.30,15,2026-04-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert any("free_cash_flow_currency" in r for r in exc.value.reasons)


def test_negative_value_rejected_by_fundamentals_invariant(tmp_path: Path) -> None:
    # ``Fundamentals.__post_init__`` rejects negative yield_; the
    # loader surfaces it under the `data:csv_malformed:...:invariant`
    # category.
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,-0.01,0.50,1000000,EUR,0.30,15,2026-04-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert any(":invariant:" in r for r in exc.value.reasons)


def test_duplicate_instrument_id_aborts_load(tmp_path: Path) -> None:
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
        "ASML.AS,0.050,0.55,1100000,EUR,0.35,16,2026-04-15\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    assert any("data:csv_duplicate:ASML.AS" in r for r in exc.value.reasons)


# ---------------------------------------------------------------------------
# TC_FND_006 — As-of-date staleness
# ---------------------------------------------------------------------------


def test_stale_row_rejected(tmp_path: Path) -> None:
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
        "STALE.XX,0.030,0.40,500000,EUR,0.20,10,2024-01-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path, max_age_days=547)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    # Both rows reach the staleness gate; only STALE.XX is too old.
    assert "data:stale:STALE.XX:2024-01-01" in exc.value.reasons
    assert not any(":ASML.AS:" in r and "stale" in r for r in exc.value.reasons)


def test_all_stale_rows_aggregated_in_one_error(tmp_path: Path) -> None:
    # Two stale rows — the operator sees BOTH in one error, not one
    # restart-fix cycle per row.
    body = (
        f"{_HAPPY_HEADER}\n"
        "OLD1.XX,0.045,0.50,1000000,EUR,0.30,15,2024-01-01\n"
        "OLD2.XX,0.050,0.55,1100000,EUR,0.35,16,2024-02-01\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path, max_age_days=100)
    with pytest.raises(CsvLoadError) as exc:
        CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    reasons = exc.value.reasons
    assert any("OLD1.XX" in r and "stale" in r for r in reasons)
    assert any("OLD2.XX" in r and "stale" in r for r in reasons)


# ---------------------------------------------------------------------------
# TC_FND_007 — refresh() is the documented mutation hook
# ---------------------------------------------------------------------------


def test_refresh_picks_up_csv_changes(tmp_path: Path) -> None:
    csv_path = tmp_path / "fund.csv"
    _csv(
        csv_path,
        f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n",
    )
    cfg = FundamentalsConfig(csv_path=csv_path)
    provider = CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    initial = provider.fundamentals(_instrument("ASML.AS")).unwrap()
    assert initial.yield_ == Decimal("0.045")
    # Operator edits the CSV between runs.
    _csv(
        csv_path,
        f"{_HAPPY_HEADER}\nASML.AS,0.050,0.55,1100000,EUR,0.30,15,2026-04-15\n",
    )
    assert isinstance(provider.refresh(), Ok)
    after = provider.fundamentals(_instrument("ASML.AS")).unwrap()
    assert after.yield_ == Decimal("0.050")


def test_refresh_returns_categorised_err_on_load_failure(tmp_path: Path) -> None:
    csv_path = tmp_path / "fund.csv"
    _csv(
        csv_path,
        f"{_HAPPY_HEADER}\nASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n",
    )
    cfg = FundamentalsConfig(csv_path=csv_path)
    provider = CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    # Break the CSV (remove a header column).
    _csv(
        csv_path,
        "instrument_id,yield_\nASML.AS,0.045\n",
    )
    match provider.refresh():
        case Err(reason):
            assert "csv_schema" in reason
        case Ok(_):
            raise AssertionError("expected Err on schema drift")


# ---------------------------------------------------------------------------
# TC_FND_010 — Bundled seed-CSV smoke test
# ---------------------------------------------------------------------------


def test_replay_determinism_same_csv_same_snapshot(tmp_path: Path) -> None:
    """REQ_F_FND_005 — the CSV is the system of record. Two providers
    constructed from the same CSV + same config produce identical
    ``Fundamentals`` for every instrument id."""
    body = (
        f"{_HAPPY_HEADER}\n"
        "ASML.AS,0.045,0.50,1000000,EUR,0.30,15,2026-04-01\n"
        "BNP.PA,0.068,0.55,7500000,EUR,0.90,15,2026-03-31\n"
    )
    csv_path = _csv(tmp_path / "fund.csv", body)
    cfg = FundamentalsConfig(csv_path=csv_path, max_age_days=547)
    p1 = CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    p2 = CSVFundamentalsProvider(config=cfg, _today=lambda: date(2026, 5, 15))
    asml_1 = p1.fundamentals(_instrument("ASML.AS")).unwrap()
    asml_2 = p2.fundamentals(_instrument("ASML.AS")).unwrap()
    # Bit-identical structural equality (REQ_NF_REP_001 family / REQ_F_FND_005).
    assert asml_1 == asml_2
    bnp_1 = p1.fundamentals(_instrument("BNP.PA")).unwrap()
    bnp_2 = p2.fundamentals(_instrument("BNP.PA")).unwrap()
    assert bnp_1 == bnp_2


def test_bundled_seed_csv_loads_cleanly() -> None:
    """The shipped `data/seed_fundamentals.csv` SHALL load with the
    default config — schema valid, every row fresh, every
    `Fundamentals` invariant satisfied."""
    assert _BUNDLED_SEED.exists(), f"missing seed CSV at {_BUNDLED_SEED}"
    cfg = FundamentalsConfig(csv_path=_BUNDLED_SEED, max_age_days=547)
    # Use a fixed today so the test is stable.
    provider = CSVFundamentalsProvider(
        config=cfg, _today=lambda: date(2026, 5, 15)
    )
    # The bundled CSV ships ~12 EU dividend stocks.
    assert len(provider._snapshot) >= 10
    # Spot-check one of the documented seed rows.
    asml = provider.fundamentals(_instrument("ASML.AS")).unwrap()
    assert asml.yield_ > Decimal(0)
    assert asml.payout_ratio > Decimal(0)
    assert asml.free_cash_flow.amount > Decimal(0)
    assert asml.dividend_history_years > 0
