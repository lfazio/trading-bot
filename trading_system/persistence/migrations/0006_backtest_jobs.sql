-- 0006_backtest_jobs.sql — CR-004 Phase B JobQueue SQLite backend
-- (REQ_F_WEB_003 / REQ_F_WEB_009 / REQ_SDD_WEB_005 / REQ_SDS_WEB_003).
--
-- Two-table layout:
--   * ``backtest_jobs``         — the immutable spec (config_dir +
--                                 window + flags), keyed on
--                                 (account_id, job_id). One row per
--                                 submission; never updated.
--   * ``backtest_job_states``   — the lifecycle ledger
--                                 (PENDING -> RUNNING -> COMPLETED |
--                                 FAILED). Append-only; the most
--                                 recent row for a (account_id,
--                                 job_id) defines the visible state.
--                                 Summary + error_category live here
--                                 so historical transitions are
--                                 audit-friendly.
--
-- Rows that survive across process restarts power REQ_SDS_WEB_003 —
-- "jobs persist + run outside the HTTP thread". A re-launched worker
-- can scan ``backtest_job_states`` for the latest state per job_id
-- and continue (RUNNING rows become FAILED with error_category
-- ``webui:worker_killed`` after the orphan sweep).

CREATE TABLE backtest_jobs (
    account_id     TEXT NOT NULL DEFAULT 'default',
    job_id         TEXT NOT NULL,
    config_dir     TEXT NOT NULL,
    start_ts       TEXT NOT NULL,
    end_ts         TEXT NOT NULL,
    with_slippage  INTEGER NOT NULL DEFAULT 0,
    submitted_at   TEXT NOT NULL,
    PRIMARY KEY (account_id, job_id)
);

CREATE TABLE backtest_job_states (
    account_id      TEXT NOT NULL DEFAULT 'default',
    job_id          TEXT NOT NULL,
    transition_seq  INTEGER NOT NULL,
    status          TEXT NOT NULL,
    transitioned_at TEXT NOT NULL,
    error_category  TEXT,
    summary_json    TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (account_id, job_id, transition_seq),
    FOREIGN KEY (account_id, job_id)
        REFERENCES backtest_jobs (account_id, job_id)
);

CREATE INDEX idx_backtest_job_states_by_job
    ON backtest_job_states (account_id, job_id, transition_seq DESC);

CREATE INDEX idx_backtest_jobs_by_submitted_at
    ON backtest_jobs (account_id, submitted_at);
