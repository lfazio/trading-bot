-- CR-019 follow-up — Paper-trading session metadata (REQ_SDD_WEB2_005
-- enrichment so the recovery wizard offers one-click resume).
--
-- The CR-008 PortfolioRepository already persists equity-curve rows
-- per account_id, so RuntimeRegistry.resume_from_persistence can
-- DISCOVER paper-* account ids at boot — but the wizard can't
-- rehydrate the runtime without the universe / strategy /
-- instrument-symbol metadata it originally supplied. This migration
-- adds that metadata so the wizard's "resume" action becomes a
-- one-click action instead of a re-input.
--
-- The table is written-once at PaperTradingRuntime construction
-- (no updates; the session's identity is immutable once started)
-- and read by RuntimeRegistry.resume_from_persistence + the
-- /operator/recovery view.
--
-- ``mode_tag`` is hardcoded to ``"paper"`` in the runtime today;
-- the column is kept for forward-compatibility with a future
-- ``LiveTradingSession`` ledger amendment (REQ_F_LIV_004 namespace).

CREATE TABLE paper_sessions (
    account_id          TEXT NOT NULL PRIMARY KEY,
    universe            TEXT NOT NULL,
    strategy_id         TEXT NOT NULL,
    instrument_symbol   TEXT NOT NULL,
    starting_capital    TEXT NOT NULL,   -- Decimal-as-TEXT
    currency            TEXT NOT NULL,
    bar_source          TEXT NOT NULL,   -- 'simulated' | 'yfinance'
    started_at          TEXT NOT NULL,   -- ISO-8601 UTC
    mode_tag            TEXT NOT NULL DEFAULT 'paper'
);

CREATE INDEX idx_paper_sessions_started_at
    ON paper_sessions (started_at);
