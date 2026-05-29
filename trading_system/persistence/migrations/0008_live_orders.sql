-- CR-019 step 2 — Live-trading order audit trail (REQ_F_LIV_007).
--
-- One row per order the live runtime submits. The pre-submit row is
-- written BEFORE BrokerAdapter.submit() with status='pending'; the
-- post-submit update flips status to 'submitted' (+ broker_order_id)
-- on Ok or 'rejected' (+ rejection_reason) on Err. A crash between
-- the pre-submit insert and the broker call leaves the row in
-- status='pending' — operators reconcile via list_pending().
--
-- `submitted_order_json` is the canonical-JSON snapshot of the Order
-- at submit-intent time so the operator can audit exactly what was
-- about to be sent; the post-submit broker_order_id is the
-- adapter-supplied identifier the operator uses to talk to the
-- broker's own audit.

CREATE TABLE live_orders (
    account_id           TEXT NOT NULL DEFAULT 'default',
    order_id             TEXT NOT NULL,
    broker_selector      TEXT NOT NULL,
    broker_order_id      TEXT,
    submitted_at         TEXT NOT NULL,
    submitted_order_json TEXT NOT NULL,
    corr_id              TEXT NOT NULL,
    status               TEXT NOT NULL,
    rejection_reason     TEXT,
    PRIMARY KEY (account_id, order_id)
);

CREATE INDEX idx_live_orders_submitted_at
    ON live_orders (submitted_at);

CREATE INDEX idx_live_orders_status
    ON live_orders (account_id, status);
