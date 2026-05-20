-- 0005_idempotency.sql — CR-004 Phase B IdempotencyStore SQLite
-- backend (REQ_F_WEB_010 / REQ_SDD_WEB_004 / REQ_SDS_WEB_004).
--
-- Keyed on (account_id, key) so two accounts can reuse the same
-- idempotency token without colliding. The body column carries the
-- canonical-JSON response payload byte-identically; status_code is
-- the HTTP status the client should see on replay. recorded_at
-- powers the TTL sweep (operators run a periodic DELETE WHERE
-- recorded_at < cutoff — out of the route's hot path).

CREATE TABLE idempotency_entries (
    account_id    TEXT NOT NULL DEFAULT 'default',
    key           TEXT NOT NULL,
    body          TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);

CREATE INDEX idx_idempotency_recorded_at
    ON idempotency_entries (account_id, recorded_at);
