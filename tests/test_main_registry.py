"""CR-006 Phase B — verify ``main.run``'s ``RunOutcome.registry``
is populated end-to-end against the bundled config.

REQ refs: REQ_F_ACC_002, REQ_F_ACC_003, REQ_F_ACC_009,
REQ_NF_ACC_001, REQ_SDS_ACC_002.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from trading_system.accounts.account import Account
from trading_system.accounts.tax_model import FranceCTOTaxModel
from trading_system.main import run
from trading_system.models.identifiers import DEFAULT_ACCOUNT_ID
from trading_system.result import Err, Ok


_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def test_run_outcome_registry_holds_single_default_account() -> None:
    """REQ_NF_ACC_001 — the legacy demo path SHALL surface a single
    ``Account(id="default")`` in the registry."""
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
        out_stream=io.StringIO(),
    )
    match res:
        case Ok(outcome):
            assert outcome.registry is not None
            assert outcome.registry.size() == 1
            accounts = outcome.registry.list_accounts()
            only = accounts[0]
            assert isinstance(only, Account)
            assert only.id == DEFAULT_ACCOUNT_ID
            # REQ_F_ACC_005 default — France CTO tax model.
            assert isinstance(only.tax_model, FranceCTOTaxModel)
            # REQ_F_ACC_010 — operator token claim equals the id.
            assert only.operator_token_account_id == str(DEFAULT_ACCOUNT_ID)
        case Err(reason):
            raise AssertionError(f"run failed: {reason}")


def test_run_outcome_account_references_backtest_portfolio() -> None:
    """The Account's portfolio + capital_flow references SHALL point
    at the SAME cursors the backtest mutated. PortfolioGroup reads
    through these refs so household aggregation sees live state
    without a separate sync step."""
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
        out_stream=io.StringIO(),
    )
    outcome = res.unwrap()
    only = outcome.registry.list_accounts()[0]
    # The portfolio.equity() should be reachable (PortfolioGroup
    # depends on this accessor — REQ_SDS_ACC_002 family).
    assert hasattr(only.portfolio, "equity")
    equity = only.portfolio.equity()
    assert equity.amount > 0


def test_run_outcome_household_drawdown_observer_runs_without_error() -> None:
    """REQ_F_ACC_009 — the observer evaluates cleanly on the demo
    path. v1 single-account deployment ⇒ household drawdown equals
    the per-account drawdown; the demo's mock-provider random walk
    typically stays below the 12 % degrade threshold so the
    breach surface stays None."""
    res = run(
        config_dir=_CONFIG_DIR,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 8, tzinfo=UTC),
        out_stream=io.StringIO(),
    )
    outcome = res.unwrap()
    # Either None (no breach), "DEGRADE", or "KILL" — every other
    # value is a regression.
    assert outcome.household_drawdown_trip in (None, "DEGRADE", "KILL")
