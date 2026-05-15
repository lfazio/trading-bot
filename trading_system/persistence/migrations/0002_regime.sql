-- 0002_regime.sql — CR-013 regime transitions table.
--
-- Persisted source of regime transitions so a restart can rehydrate
-- the TransitionTracker cursor with the latest persisted regime
-- (REQ_SDD_RGM_005). Schema mirrors the 0001_init.sql account-aware
-- pattern: every row carries ``account_id`` defaulting to ``'default'``
-- for CR-006-readiness (REQ_F_PER_009 / REQ_SDD_PER_008).

CREATE TABLE transitions (
    account_id           TEXT NOT NULL DEFAULT 'default',
    at                   TEXT NOT NULL,
    from_regime          TEXT NOT NULL,
    to_regime            TEXT NOT NULL,
    confirmation_periods INTEGER NOT NULL,
    snapshot_id          TEXT NOT NULL,
    PRIMARY KEY (account_id, at)
);

CREATE INDEX idx_transitions_at ON transitions (account_id, at);
