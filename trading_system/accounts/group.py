"""``PortfolioGroup`` — read-only household aggregator over the
:class:`AccountRegistry`.

Rebuilds aggregates on every call so the view cannot drift from
per-account state (REQ_F_ACC_007 / REQ_SDD_ACC_004). No write
methods — mutation goes through individual accounts. Currency
normalisation against the system base happens via an injected
``FxConverter`` callable so the aggregator stays decoupled from the
actual FX-rate plumbing.

REQ refs: REQ_F_ACC_007, REQ_SDS_ACC_002, REQ_SDD_ACC_004.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from trading_system.accounts.registry import AccountRegistry
from trading_system.models.identifiers import InstrumentId
from trading_system.models.money import Currency, Money
from trading_system.result import Err, Ok, Result


@runtime_checkable
class FxConverter(Protocol):
    """Pure function that converts a :class:`Money` into the target
    currency. v1 callers pass either an identity converter (when every
    account is in the same currency) or an FX-rate-backed converter.
    Missing rates SHALL surface as ``Err("accounts:fx_missing:<from>:
    <to>")`` (REQ_SDD_ACC_004)."""

    def convert(
        self, amount: Money, *, target_currency: Currency
    ) -> Result[Money, str]:
        ...


@dataclass(slots=True)
class IdentityFxConverter:
    """Trivial converter: amounts already in ``target_currency`` pass
    through unchanged; any other currency surfaces an Err. v1 default
    for single-currency deployments."""

    def convert(
        self, amount: Money, *, target_currency: Currency
    ) -> Result[Money, str]:
        if amount.currency == target_currency:
            return Ok(amount)
        return Err(
            f"accounts:fx_missing:{amount.currency.value}:{target_currency.value}"
        )


@dataclass(slots=True)
class PortfolioGroup:
    """Read-only aggregator. ``base_currency`` is the household's
    canonical currency; everything is normalised against it before
    aggregation."""

    registry: AccountRegistry
    base_currency: Currency = Currency.EUR
    fx: FxConverter = field(default_factory=IdentityFxConverter)

    def household_equity(self) -> Result[Money, str]:
        """Sum of every account's after-tax equity, normalised to
        ``base_currency``. Returns ``Err`` on the first FX miss so the
        dashboard never renders a stale / silently-zeroed value."""
        total = Money(Decimal(0), self.base_currency)
        for account in self.registry.list_accounts():
            equity = _account_equity(account)
            match self.fx.convert(equity, target_currency=self.base_currency):
                case Err(reason):
                    return Err(reason)
                case Ok(converted):
                    total = total + converted
        return Ok(total)

    def exposure_by_instrument(self) -> Result[Mapping[InstrumentId, Money], str]:
        """Aggregate per-instrument exposure across accounts. Returns
        a fresh mapping; never the registry's internal state."""
        out: dict[InstrumentId, Money] = {}
        for account in self.registry.list_accounts():
            for instrument_id, exposure in _account_positions(account):
                match self.fx.convert(exposure, target_currency=self.base_currency):
                    case Err(reason):
                        return Err(reason)
                    case Ok(converted):
                        existing = out.get(instrument_id)
                        if existing is None:
                            out[instrument_id] = converted
                        else:
                            out[instrument_id] = existing + converted
        return Ok(out)

    def household_drawdown(self) -> Result[Decimal, str]:
        """Aggregate household drawdown across accounts.

        v1 takes the max of per-account drawdowns as the household
        figure — a conservative aggregator that triggers the
        household DEGRADE / KILL thresholds on any account's
        drawdown. The Phase-6 follow-up may replace this with a
        consolidated-equity-curve drawdown once the per-account
        curves are wired through the persistence layer.
        """
        peak = Decimal(0)
        for account in self.registry.list_accounts():
            current = _account_drawdown(account)
            if current > peak:
                peak = current
        return Ok(peak)


# ---------------------------------------------------------------------------
# Internal accessors — kept loose because the Phase-6 foundation slice
# doesn't bind to the concrete Portfolio / CapitalFlow types. The
# follow-up slice tightens these to the structural types.
# ---------------------------------------------------------------------------


def _account_equity(account: Any) -> Money:
    """Read the account's equity from its portfolio. v1 callers
    supply a portfolio with an ``equity()`` method returning Money."""
    portfolio = account.portfolio
    if hasattr(portfolio, "equity"):
        return portfolio.equity()
    raise TypeError(
        "Account.portfolio must expose .equity() -> Money "
        f"(got {type(portfolio).__name__})"
    )


def _account_positions(account: Any) -> Iterator[tuple[InstrumentId, Money]]:
    """Iterate (instrument_id, exposure_in_account_currency) pairs.
    v1 callers supply a portfolio with a ``positions_by_instrument``
    accessor; legacy callers without that accessor surface an empty
    iterator so the aggregator still works against ``MagicMock``-
    style test doubles."""
    portfolio = account.portfolio
    if hasattr(portfolio, "positions_by_instrument"):
        yield from portfolio.positions_by_instrument()
    return  # legacy or test-double — empty iter


def _account_drawdown(account: Any) -> Decimal:
    """Read the account's current drawdown as a non-negative
    ``Decimal``. v1 callers supply a portfolio with a
    ``drawdown_pct()`` accessor."""
    portfolio = account.portfolio
    if hasattr(portfolio, "drawdown_pct"):
        return portfolio.drawdown_pct()
    return Decimal(0)
