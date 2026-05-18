-- 0004_quant.sql — CR-002 Phase B HypothesisRepository backend
-- (REQ_SDD_QNT_007 / REQ_F_QNT_001).
--
-- Two tables: ``hypotheses`` holds the frozen Hypothesis row;
-- ``hypothesis_transitions`` is the append-only audit log
-- (PENDING → VALIDATED / REJECTED). The library's current_state()
-- lookup is "latest transition for the id; fall back to initial
-- state". Mirrors the in-memory v1's data model.
--
-- Schema follows the account-aware pattern: every row carries
-- ``account_id`` defaulting to ``'default'`` (REQ_F_PER_009 /
-- REQ_SDD_PER_008). REQ_NF_QNT_001 (offline-only / no runtime
-- import) is enforced at the import-graph level, not the schema
-- level — the persistence layer ships the table; whether a
-- runtime module touches it is a separate audit.

CREATE TABLE hypotheses (
    account_id              TEXT NOT NULL DEFAULT 'default',
    hypothesis_id           TEXT NOT NULL,
    claim                   TEXT NOT NULL,
    falsification_criterion TEXT NOT NULL,
    metric                  TEXT NOT NULL,
    expected_direction      TEXT NOT NULL,
    operator_rationale      TEXT NOT NULL,
    dataset_start           TEXT NOT NULL,
    dataset_end             TEXT NOT NULL,
    dataset_frequency       TEXT NOT NULL,
    initial_state           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    PRIMARY KEY (account_id, hypothesis_id)
);

CREATE INDEX idx_hypotheses_created_at
    ON hypotheses (account_id, created_at);

CREATE TABLE hypothesis_transitions (
    account_id      TEXT NOT NULL,
    hypothesis_id   TEXT NOT NULL,
    transitioned_at TEXT NOT NULL,
    new_state       TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, hypothesis_id, transitioned_at),
    FOREIGN KEY (account_id, hypothesis_id)
        REFERENCES hypotheses (account_id, hypothesis_id)
);

CREATE INDEX idx_hypothesis_transitions_at
    ON hypothesis_transitions (account_id, transitioned_at);
