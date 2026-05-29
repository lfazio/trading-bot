-- CR-024 — Operator-token revocation list (REQ_F_TOK_002 / REQ_SDD_TOK_002).
--
-- One row per revoked token id (`jti`). The `jti` is a 32-char
-- `uuid4().hex` segment embedded in the token's signed payload
-- (REQ_SDD_TOK_001); the revocation list is keyed on
-- `(account_id, jti)` so two accounts cannot collide on the same
-- random id.
--
-- The repository's contract is append-only at the row grain:
-- re-revoking the same `(account_id, jti)` is idempotent
-- (ON CONFLICT DO NOTHING). The verifier's revocation check is
-- O(1) via an in-memory set warmed at startup; this table is the
-- system of record for replay across process restarts
-- (REQ_NF_PER_001).

CREATE TABLE operator_token_revocations (
    account_id  TEXT NOT NULL DEFAULT 'default',
    jti         TEXT NOT NULL,
    revoked_at  TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, jti)
);

CREATE INDEX idx_operator_token_revocations_at
    ON operator_token_revocations (revoked_at);
