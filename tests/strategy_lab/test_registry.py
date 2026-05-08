"""Tests for ``trading_system.strategy_lab.registry``.

REQ refs: REQ_F_MTO_005 (validated entries immutable),
REQ_NF_REP_001 (sha + config_hash + seed for replay),
REQ_SDS_CRS_003, REQ_SDD_DAT_004.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_system.models.identifiers import StrategyId
from trading_system.result import Err, Nothing, Ok, Some
from trading_system.strategy_lab.metrics import StrategyMetrics
from trading_system.strategy_lab.registry import Registry, RegistryEntry


def _metrics() -> StrategyMetrics:
    return StrategyMetrics(
        net_after_tax_return=Decimal("0.10"),
        sharpe=Decimal("1.0"),
        stability=Decimal("0.7"),
        dd_penalty=Decimal("0.1"),
        max_drawdown=Decimal("0.1"),
        turnover=Decimal("8"),
        regime_stability=Decimal("0.6"),
        leverage=Decimal("1"),
        parameter_sensitivity=Decimal("0.2"),
        risk=Decimal("0.10"),
        return_=Decimal("0.10"),
    )


def _entry(
    strategy_id: str = "core_v1",
    *,
    git_sha: str = "abc123",
    config_hash: str = "cfg-1",
    seed: int = 42,
    validated: bool = False,
) -> RegistryEntry:
    return RegistryEntry(
        strategy_id=StrategyId(strategy_id),
        git_sha=git_sha,
        config_hash=config_hash,
        seed=seed,
        metrics=_metrics(),
        validated=validated,
        created_at=datetime(2026, 5, 8, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# RegistryEntry validation
# ---------------------------------------------------------------------------


class TestRegistryEntry:
    def test_empty_sha_rejected(self) -> None:
        with pytest.raises(ValueError, match="git_sha"):
            _entry(git_sha="")

    def test_empty_config_hash_rejected(self) -> None:
        with pytest.raises(ValueError, match="config_hash"):
            _entry(config_hash="")


# ---------------------------------------------------------------------------
# store + immutability
# ---------------------------------------------------------------------------


class TestStore:
    def test_first_store_succeeds(self) -> None:
        r = Registry()
        match r.store(_entry()):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"expected Ok, got Err({e!r})")
        assert r.get(StrategyId("core_v1")) == Some(_entry())

    def test_validated_entries_immutable(self) -> None:
        r = Registry()
        r.store(_entry(validated=True))
        # Attempting to overwrite with another validated row fails.
        match r.store(_entry(git_sha="def456", validated=True)):
            case Err(reason):
                assert reason.startswith("registry:validated_immutable")
            case Ok(_):
                raise AssertionError("expected Err")

    def test_experimental_entries_replaceable(self) -> None:
        r = Registry()
        r.store(_entry(git_sha="abc", validated=False))
        r.store(_entry(git_sha="def", validated=False))
        e = r.get(StrategyId("core_v1"))
        match e:
            case Some(entry):
                assert entry.git_sha == "def"
            case Nothing():
                raise AssertionError("expected Some")

    def test_experimental_can_be_replaced_by_validated(self) -> None:
        r = Registry()
        r.store(_entry(validated=False))
        match r.store(_entry(git_sha="def", validated=True)):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")


# ---------------------------------------------------------------------------
# mark_validated
# ---------------------------------------------------------------------------


class TestMarkValidated:
    def test_promotes_experimental(self) -> None:
        r = Registry()
        r.store(_entry(validated=False))
        match r.mark_validated(StrategyId("core_v1")):
            case Ok(_):
                pass
            case Err(e):
                raise AssertionError(f"unexpected Err: {e}")
        match r.get(StrategyId("core_v1")):
            case Some(entry):
                assert entry.validated is True
                assert entry.git_sha == "abc123"  # other fields preserved
            case Nothing():
                raise AssertionError("expected Some")

    def test_unknown_id_returns_err(self) -> None:
        r = Registry()
        match r.mark_validated(StrategyId("missing")):
            case Err(reason):
                assert reason == "registry:not_found:missing"
            case Ok(_):
                raise AssertionError("expected Err")

    def test_already_validated_returns_err(self) -> None:
        r = Registry()
        r.store(_entry(validated=True))
        match r.mark_validated(StrategyId("core_v1")):
            case Err(reason):
                assert reason == "registry:already_validated:core_v1"
            case Ok(_):
                raise AssertionError("expected Err")


# ---------------------------------------------------------------------------
# baseline + listing
# ---------------------------------------------------------------------------


class TestBaselineAndListing:
    def test_no_baseline_yields_nothing(self) -> None:
        assert Registry().current() == Nothing()

    def test_set_baseline_requires_validated(self) -> None:
        r = Registry()
        r.store(_entry(validated=False))
        match r.set_baseline(StrategyId("core_v1")):
            case Err(reason):
                assert reason.startswith("registry:not_validated")
            case Ok(_):
                raise AssertionError("expected Err")

    def test_set_baseline_then_current(self) -> None:
        r = Registry()
        r.store(_entry(validated=True))
        r.set_baseline(StrategyId("core_v1"))
        match r.current():
            case Some(entry):
                assert entry.strategy_id == StrategyId("core_v1")
            case Nothing():
                raise AssertionError("expected Some")

    def test_list_validated_sorted_by_id(self) -> None:
        r = Registry()
        r.store(_entry("zeta_v1", validated=True))
        r.store(_entry("alpha_v1", validated=True))
        r.store(_entry("beta_exp", validated=False))
        validated = r.list_validated()
        assert [e.strategy_id for e in validated] == [
            StrategyId("alpha_v1"),
            StrategyId("zeta_v1"),
        ]
        experimental = r.list_experimental()
        assert [e.strategy_id for e in experimental] == [StrategyId("beta_exp")]
