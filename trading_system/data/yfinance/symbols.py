"""Yahoo-suffix lookup for ``Instrument`` -> Yahoo ticker.

Yahoo Finance disambiguates listings with exchange suffixes: the
Amsterdam ASML listing is ``ASML.AS``, the Paris BNP listing is
``BNP.PA``, the London Diageo listing is ``DGE.L``, etc. US listings
carry no suffix. The mapping is small and stable; an unknown
exchange returns ``Err`` rather than silently substituting.

REQ refs:
- REQ_F_DAT_002 — adapter sources OHLCV bars from Yahoo Finance.
- REQ_SDD_DAT_011 — coverage at minimum AS / PA / DE / L plus US.
- REQ_SDD_DAT_012 — closed error category set; unknown exchange
  surfaces ``Err("data:unknown_exchange:<exchange>")``.
"""

from __future__ import annotations

from trading_system.models.instrument import Instrument
from trading_system.result import Err, Ok, Result

# Exchange suffix table. Keys are the ``Instrument.exchange`` values
# the rest of the system uses. The empty string suffix marks
# "no suffix" (US listings).
_SUFFIX_BY_EXCHANGE: dict[str, str] = {
    # Euronext
    "AS": "AS",  # Amsterdam
    "PA": "PA",  # Paris
    "BR": "BR",  # Brussels
    "LS": "LS",  # Lisbon
    # Deutsche Börse
    "DE": "DE",  # XETRA
    "F": "F",  # Frankfurt
    # London Stock Exchange
    "L": "L",
    # Swiss
    "SW": "SW",
    # US — bare ticker
    "NYSE": "",
    "NASDAQ": "",
    "AMEX": "",
    "ARCA": "",
    "US": "",
}


def yahoo_symbol_for(instrument: Instrument) -> Result[str, str]:
    """Build the Yahoo Finance ticker for ``instrument``.

    Returns ``Ok("ASML.AS")`` for known exchanges; ``Err("data:unknown_exchange:<exch>")``
    for any exchange not in the table — callers SHALL NOT fall back
    to the bare symbol because that would silently misroute to the
    US listing.
    """
    suffix = _SUFFIX_BY_EXCHANGE.get(instrument.exchange)
    if suffix is None:
        return Err(f"data:unknown_exchange:{instrument.exchange}")
    if suffix == "":
        return Ok(instrument.symbol)
    return Ok(f"{instrument.symbol}.{suffix}")
