-- 0001_init.sql — CR-008 Phase-5 initial schema (REQ_F_PER_009).
--
-- Every table carries ``account_id`` with ``DEFAULT 'default'`` so
-- CR-006's Phase-6 implementation is a code-only change (no schema
-- migration). Money amounts are stored as TEXT to preserve full
-- Decimal precision per REQ_F_PER_005; datetimes are ISO-8601 with
-- explicit timezone.

-- ---------------------------------------------------------------
-- Portfolio: equity curve points + position snapshots.
-- ---------------------------------------------------------------
CREATE TABLE equity_points (
    account_id                TEXT NOT NULL DEFAULT 'default',
    at                        TEXT NOT NULL,
    equity_gross_amount       TEXT NOT NULL,
    equity_gross_currency     TEXT NOT NULL,
    equity_after_tax_amount   TEXT NOT NULL,
    equity_after_tax_currency TEXT NOT NULL,
    drawdown_pct              TEXT NOT NULL,
    PRIMARY KEY (account_id, at)
);

CREATE INDEX idx_equity_points_at ON equity_points (account_id, at);

-- ---------------------------------------------------------------
-- Registry: strategy entries + promotion audit log.
-- ---------------------------------------------------------------
CREATE TABLE strategy_registry (
    account_id   TEXT NOT NULL DEFAULT 'default',
    strategy_id  TEXT NOT NULL,
    git_sha      TEXT NOT NULL,
    config_hash  TEXT NOT NULL,
    seed         INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    validated    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, strategy_id)
);

CREATE TABLE registry_promotions (
    account_id          TEXT NOT NULL DEFAULT 'default',
    strategy_id         TEXT NOT NULL,
    promoted_by         TEXT NOT NULL,
    promoted_at         TEXT NOT NULL,
    promoter_token_hash TEXT NOT NULL,
    promotion_rationale TEXT NOT NULL,
    PRIMARY KEY (account_id, strategy_id, promoted_at)
);

-- ---------------------------------------------------------------
-- Backtest archive: keyed on (strategy_id, sha, config_hash, seed)
-- so REQ_NF_REP_001 holds — same tuple replays bit-identically.
-- ---------------------------------------------------------------
CREATE TABLE backtest_results (
    account_id   TEXT NOT NULL DEFAULT 'default',
    strategy_id  TEXT NOT NULL,
    git_sha      TEXT NOT NULL,
    config_hash  TEXT NOT NULL,
    seed         INTEGER NOT NULL,
    result_json  TEXT NOT NULL,
    archived_at  TEXT NOT NULL,
    PRIMARY KEY (account_id, strategy_id, git_sha, config_hash, seed)
);

-- ---------------------------------------------------------------
-- Kill-switch snapshots — drop-in for FileSnapshotSink behind the
-- existing SnapshotSink Protocol (REQ_F_PER_008 / REQ_SDD_PER_007).
-- ---------------------------------------------------------------
CREATE TABLE ks_snapshots (
    account_id    TEXT NOT NULL DEFAULT 'default',
    snapshot_id   TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (account_id, snapshot_id)
);

-- ---------------------------------------------------------------
-- Capital flow: external-capital injection ledger (REQ_F_CFL_001).
-- ---------------------------------------------------------------
CREATE TABLE capital_flow_initial (
    account_id      TEXT NOT NULL DEFAULT 'default' PRIMARY KEY,
    initial_amount  TEXT NOT NULL,
    initial_currency TEXT NOT NULL
);

CREATE TABLE capital_flow_injections (
    account_id     TEXT NOT NULL DEFAULT 'default',
    at             TEXT NOT NULL,
    amount         TEXT NOT NULL,
    currency       TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, at, source)
);
