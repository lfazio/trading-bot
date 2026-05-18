-- 0003_approvals.sql — CR-001 Phase B audit trail for the trade-
-- approval gate (REQ_F_NOT_004 / REQ_F_NOT_005 / REQ_NF_NOT_003).
--
-- One row per ``TradeApprovalRequest`` / ``ApprovalResponse`` cycle.
-- The raw operator token SHALL NEVER be persisted — only its SHA-256
-- hash lands in ``operator_token_hash`` (REQ_F_NOT_005 / REQ_NF_NOT_003;
-- mirrors the existing ``promoter_token_hash`` column in 0001_init.sql).
--
-- Schema mirrors the account-aware pattern: every row carries
-- ``account_id`` defaulting to ``'default'`` (REQ_F_PER_009 /
-- REQ_SDD_PER_008).

CREATE TABLE approval_requests (
    account_id         TEXT NOT NULL DEFAULT 'default',
    request_id         TEXT NOT NULL,
    instrument_id      TEXT NOT NULL,
    side               TEXT NOT NULL,
    quantity           TEXT NOT NULL,
    expected_loss_amount   TEXT NOT NULL,
    expected_loss_currency TEXT NOT NULL,
    rationale_digest   TEXT NOT NULL,
    requested_at       TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    PRIMARY KEY (account_id, request_id)
);

CREATE INDEX idx_approval_requests_requested_at
    ON approval_requests (account_id, requested_at);

CREATE TABLE approval_responses (
    account_id           TEXT NOT NULL DEFAULT 'default',
    request_id           TEXT NOT NULL,
    approved             INTEGER NOT NULL,  -- 0/1
    operator_id          TEXT NOT NULL,
    operator_token_hash  TEXT NOT NULL,     -- SHA-256(token); raw token NEVER persisted
    responded_at         TEXT NOT NULL,
    rejection_reason     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, request_id),
    FOREIGN KEY (account_id, request_id)
        REFERENCES approval_requests (account_id, request_id)
);

CREATE INDEX idx_approval_responses_responded_at
    ON approval_responses (account_id, responded_at);
