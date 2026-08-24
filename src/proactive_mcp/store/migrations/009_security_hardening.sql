-- Security hardening: coalesced evaluation, delivery receipt leases, bounded dates.
CREATE TABLE evaluation_gate (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    last_started_at TEXT NOT NULL
);

CREATE TABLE situation_delivery_claims (
    claim_token TEXT NOT NULL,
    situation_id INTEGER NOT NULL UNIQUE REFERENCES situations(id),
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(claim_token, situation_id),
    CHECK(expires_at > claimed_at)
);
CREATE INDEX idx_situation_delivery_claims_expiry
ON situation_delivery_claims(expires_at);

CREATE INDEX idx_situations_pending_delivery
ON situations(state, priority, detected_at, id);

CREATE TRIGGER memory_items_security_bounds_insert
BEFORE INSERT ON memory_items
WHEN NEW.lead_days < 0 OR NEW.lead_days > 366
    OR NEW.date_anchor = '9999-12-31'
BEGIN
    SELECT RAISE(ABORT, 'memory date exceeds supported bounds');
END;

CREATE TRIGGER memory_items_security_bounds_update
BEFORE UPDATE OF lead_days, date_anchor ON memory_items
WHEN NEW.lead_days < 0 OR NEW.lead_days > 366
    OR NEW.date_anchor = '9999-12-31'
BEGIN
    SELECT RAISE(ABORT, 'memory date exceeds supported bounds');
END;
