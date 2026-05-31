"""CR-019 §6 — paper-session row builder.

Lives under ``webapp/runtimes/`` (the documented carve-out for
engine-side reach) so the view-layer onboarding handler can keep
its imports clean — view routers SHALL NOT import
``trading_system.persistence.*`` directly per REQ_SDD_FAS_001.

The builder takes the wizard inputs + returns a ready-to-persist
``PaperSessionRow``. The wizard then hands the row to the
Protocol-shaped ``paper_session_repository`` slot on
``app.state``.
"""

from __future__ import annotations

from datetime import datetime

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.money import Money
from trading_system.persistence.repositories.paper_sessions import (
    PaperSessionRow,
)


def build_paper_session_row(
    *,
    account_id: AccountId,
    universe: str,
    strategy_id: StrategyId,
    instrument_symbol: str,
    starting_capital: Money,
    bar_source: str,
    started_at: datetime,
) -> PaperSessionRow:
    """Construct a ``PaperSessionRow`` from the wizard inputs.

    Thin wrapper: the row's invariants are enforced by
    ``PaperSessionRow.__post_init__``. Lives here so the view
    layer can call it without importing the persistence package
    directly.
    """
    return PaperSessionRow(
        account_id=account_id,
        universe=universe,
        strategy_id=strategy_id,
        instrument_symbol=instrument_symbol,
        starting_capital=starting_capital,
        bar_source=bar_source,
        started_at=started_at,
    )
