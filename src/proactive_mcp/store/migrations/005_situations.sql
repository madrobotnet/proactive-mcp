-- M3 situation engine: detected situations, their delivery state machine,
-- and type-scope mutes.
CREATE TABLE situations (
    id INTEGER PRIMARY KEY,
    situation_type TEXT NOT NULL CHECK(
        situation_type IN ('reply_deadline', 'calendar_conflict', 'personal_occasion')
    ),
    dedupe_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(
        state IN (
            'pending', 'delivered', 'acknowledged', 'snoozed',
            'muted', 'resolved', 'expired'
        )
    ),
    priority TEXT NOT NULL CHECK(priority IN ('critical', 'high', 'routine')),
    title TEXT NOT NULL,
    why_now TEXT NOT NULL,
    evidence TEXT NOT NULL,
    expires_at TEXT,
    detected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    acknowledged_at TEXT,
    snoozed_until TEXT,
    resolved_at TEXT,
    expired_at TEXT,
    muted_at TEXT
);
CREATE INDEX idx_situations_state ON situations(state);
CREATE INDEX idx_situations_type_state ON situations(situation_type, state);
CREATE INDEX idx_situations_delivered_at ON situations(delivered_at);

CREATE TABLE situation_type_mutes (
    situation_type TEXT PRIMARY KEY CHECK(
        situation_type IN ('reply_deadline', 'calendar_conflict', 'personal_occasion')
    ),
    created_at TEXT NOT NULL
);
