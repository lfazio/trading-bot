-- CR-029 — Multi-instrument bar persistence (REQ_F_PER_011 / REQ_SDD_PER_010).
--
-- One row per (account_id, instrument_id, bar_at) tuple. The
-- CR-026 multi-instrument paper runtime polls every universe
-- symbol per tick + this table is the persistence backstop so
-- the operator can ask "what was BNP.PA doing when MC.PA was
-- BOUGHT?" with a deterministic answer.
--
-- The Decimal-as-TEXT discipline (REQ_F_PER_005) keeps the cache
-- + the database byte-identical for the same cached bar — a
-- duplicate-PK write on the same key is idempotent because the
-- CR-021 yfinance cache produces identical bytes (REQ_F_PER_012).
--
-- ``idx_instrument_bars_by_account_at`` backs the cross-symbol
-- slice query ``bars_at(account_id, at)`` — "what was the whole
-- universe doing at time T" — so it stays O(log n) without a
-- table scan.

CREATE TABLE instrument_bars (
    account_id    TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    bar_at        TEXT NOT NULL,    -- ISO-8601 UTC
    open          TEXT NOT NULL,    -- Decimal-as-TEXT
    high          TEXT NOT NULL,
    low           TEXT NOT NULL,
    close         TEXT NOT NULL,
    volume        TEXT NOT NULL,
    PRIMARY KEY (account_id, instrument_id, bar_at)
);

CREATE INDEX idx_instrument_bars_by_account_at
    ON instrument_bars (account_id, bar_at);
