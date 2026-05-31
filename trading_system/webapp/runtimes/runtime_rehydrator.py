"""CR-019 §6 — one-click paper-session rehydration.

After a webapp restart the recovery wizard offers a "Resume"
button for every discovered paper-* account_id. The button POSTs
to ``/operator/paper-sessions/{account_id}/rehydrate``; the
handler calls :func:`rehydrate_paper_session` here which reads
the wizard inputs from the ``PaperSessionRepository`` + rebuilds
the runtime via the same `build_runtime` + bar-source + strategy
factory the wizard's finish handler uses.

Lives under ``webapp/runtimes/`` so the view layer keeps its
imports clean — view routers SHALL NOT reach
``trading_system.persistence.*`` or ``trading_system.strategies.*``
directly per REQ_SDD_FAS_001. The route consumes this helper as
a Protocol-shaped function.

REQ refs:
- REQ_F_PAP_003 — persisted session resumes cleanly after webapp
  restart "without operator action" (now actually true — pre-§6
  the operator had to re-run the wizard).
- REQ_SDD_WEB2_005 — `resume_from_persistence` enrichment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trading_system.models.identifiers import AccountId, StrategyId
from trading_system.models.phase import MarketRegime
from trading_system.result import Err, Ok, Result
from trading_system.webapp.runtimes.paper_trading import (
    PaperTradingRuntime,
    PaperTradingSession,
    build_runtime,
)


@runtime_checkable
class _PaperSessionRepoView(Protocol):
    """Subset of ``PaperSessionRepository`` the rehydrator needs."""

    def get(self, account_id: AccountId): ...


@runtime_checkable
class _RuntimeRegistryView(Protocol):
    """Subset of ``RuntimeRegistry`` the rehydrator consults +
    mutates."""

    def status(self, account_id: AccountId): ...

    def start(self, runtime: PaperTradingRuntime): ...


@dataclass(slots=True)
class RehydrateRequest:
    """Inputs the route hands to :func:`rehydrate_paper_session`."""

    account_id: AccountId
    paper_session_repo: _PaperSessionRepoView
    runtime_registry: _RuntimeRegistryView
    instrument_bar_repo: object | None = None


def rehydrate_paper_session(
    request: RehydrateRequest,
) -> Result[PaperTradingRuntime, str]:
    """Rebuild a paper-trading runtime from persisted session
    metadata + register it with the runtime registry.

    Returns ``Ok(runtime)`` on success; categorised ``Err`` on:
    - ``paper:rehydrate:already_running:<account_id>`` — the
      session is already live; operator should refresh the
      dashboard instead.
    - ``paper:rehydrate:session_not_found:<account_id>`` — no
      metadata row for that account_id (e.g., pre-§6 session
      that never wrote one).
    - ``paper:rehydrate:bad_strategy:<id>`` — strategy factory
      returned None (operator-supplied id no longer exists).
    - ``paper:rehydrate:runtime_failed:<reason>`` — build_runtime
      bubbled an Err.
    - ``paper:rehydrate:register_failed:<reason>`` — registry
      rejected the start (race with another rehydration).
    """
    # 1. Idempotency — if the session is already live, surface
    #    that to the operator instead of double-starting.
    status = request.runtime_registry.status(request.account_id)
    if hasattr(status, "value") and status.value is not None:
        return Err(
            f"paper:rehydrate:already_running:{request.account_id}"
        )

    # 2. Read the persisted metadata.
    meta_result = request.paper_session_repo.get(request.account_id)
    if not isinstance(meta_result, Ok):
        return Err(
            f"paper:rehydrate:session_not_found:{request.account_id}"
        )
    row = meta_result.value
    if row is None:
        return Err(
            f"paper:rehydrate:session_not_found:{request.account_id}"
        )

    # 3. Resolve the instrument + universe via the existing
    #    universe loader.
    from trading_system.webapp.runtimes.universe_loader import (
        first_instrument_or_fallback,
        index_for_universe,
        stocks_for_universe,
    )

    instrument = _instrument_for(row, first_instrument_or_fallback)
    reference_index = index_for_universe(row.universe)

    # 4. Build the bar source.
    bar_source = _build_bar_source(
        row.bar_source,
        instrument=instrument,
        account_id=request.account_id,
    )

    # 5. Build the strategy.
    from trading_system.webapp.runtimes.strategy_factory import (
        build_strategy,
    )

    strategy = build_strategy(
        str(row.strategy_id),
        strategy_id=StrategyId(str(row.strategy_id)),
    )
    if strategy is None:
        return Err(f"paper:rehydrate:bad_strategy:{row.strategy_id}")

    # 6. Construct phase constraints from starting capital.
    from trading_system.webapp.runtimes.phase_loader import (
        phase_constraints_for_capital,
    )

    constraints = phase_constraints_for_capital(row.starting_capital.amount)

    # 7. Rebuild the runtime via the wizard's same factory.
    session = PaperTradingSession(
        account_id=request.account_id,
        universe=row.universe,
        strategy_id=row.strategy_id,
        starting_capital=row.starting_capital,
        started_at=datetime.now(tz=UTC),  # fresh "resumed" stamp
    )
    runtime_result = build_runtime(
        session=session,
        instrument=instrument,
        strategy=strategy,
        bar_source=bar_source,
        phase_constraints=constraints,
        regime=MarketRegime.SIDEWAYS,
    )
    if not isinstance(runtime_result, Ok):
        return Err(
            f"paper:rehydrate:runtime_failed:{runtime_result.error}"
        )
    runtime = runtime_result.value

    # 8. Wire the same slots the wizard wires.
    if row.bar_source == "simulated":
        from trading_system.webapp.runtimes.simulated_bar_source import (
            SimulatedMarketDataProvider,
        )

        runtime.market_data_provider = SimulatedMarketDataProvider(
            source=bar_source, instrument=instrument
        )
    else:
        runtime.market_data_provider = getattr(
            bar_source, "_provider", None
        )
    runtime.reference_index = reference_index

    universe_stocks = stocks_for_universe(row.universe)
    if universe_stocks:
        runtime.universe = tuple(
            sorted(universe_stocks, key=lambda s: s.symbol)
        )

    if request.instrument_bar_repo is not None:
        runtime.instrument_bar_repo = request.instrument_bar_repo

    # 9. Register against the runtime registry. A race (another
    #    rehydration concurrent with this one) ⇒ registry's
    #    duplicate-id rejection surfaces here.
    start_result = request.runtime_registry.start(runtime)
    if isinstance(start_result, Err):
        return Err(
            f"paper:rehydrate:register_failed:{start_result.error}"
        )
    return Ok(runtime)


def _instrument_for(row, first_instrument_or_fallback):  # type: ignore[no-untyped-def]
    """Resolve the instrument from the persisted symbol — fall
    back to the universe's first lex-sorted stock when the
    persisted symbol isn't in the universe (operator may have
    edited the universe YAML between sessions)."""
    from trading_system.models.identifiers import InstrumentId
    from trading_system.models.instrument import InstrumentClass, Stock
    from trading_system.models.money import Currency

    fallback = Stock(
        id=InstrumentId(f"{row.instrument_symbol}.PA"),
        symbol=row.instrument_symbol,
        exchange="PA",
        currency=Currency.EUR,
        cls=InstrumentClass.STOCK,
        isin=f"INDEX_{row.instrument_symbol}",
        sector="unknown",
        country="FR",
    )
    return first_instrument_or_fallback(row.universe, fallback=fallback)


def _build_bar_source(kind: str, *, instrument, account_id):  # type: ignore[no-untyped-def]
    """Same shape as the wizard's `_build_bar_source` — duplicated
    here so the rehydrator stays self-contained (the wizard's
    private helper lives under ``webapp/routers/views/`` which
    SHALL NOT be imported from ``webapp/runtimes/``)."""
    if kind == "yfinance":
        from trading_system.webapp.runtimes.yfinance_bar_source import (
            build_yfinance_bar_source,
        )

        return build_yfinance_bar_source(instrument=instrument)

    from trading_system.webapp.runtimes.simulated_bar_source import (
        SimulatedBarSource,
    )

    return SimulatedBarSource(
        instrument_id=instrument.id,
        seed=abs(hash(str(account_id))) % (2**31),
    )
