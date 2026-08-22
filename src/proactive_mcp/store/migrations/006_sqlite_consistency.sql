-- Generation ordering, immutable delivery history, and one-shot snooze claims.
CREATE TABLE source_detection_generations (
    source TEXT PRIMARY KEY CHECK(source IN ('gmail', 'calendar')),
    issued_generation INTEGER NOT NULL DEFAULT 0 CHECK(issued_generation >= 0),
    applied_generation INTEGER NOT NULL DEFAULT 0 CHECK(applied_generation >= 0),
    status TEXT CHECK(status IN ('complete', 'degraded')),
    CHECK(applied_generation <= issued_generation)
);

CREATE TABLE situation_deliveries (
    id INTEGER PRIMARY KEY,
    situation_id INTEGER NOT NULL REFERENCES situations(id),
    delivered_at TEXT NOT NULL,
    priority TEXT NOT NULL CHECK(priority IN ('critical', 'high', 'routine'))
);
CREATE INDEX idx_situation_deliveries_at
    ON situation_deliveries(delivered_at);
CREATE INDEX idx_situation_deliveries_situation
    ON situation_deliveries(situation_id);

INSERT INTO situation_deliveries (situation_id, delivered_at, priority)
SELECT id, delivered_at, priority
FROM situations
WHERE delivered_at IS NOT NULL;

CREATE TRIGGER situation_deliveries_no_update
BEFORE UPDATE ON situation_deliveries
BEGIN
    SELECT RAISE(ABORT, 'situation delivery history is immutable');
END;

CREATE TRIGGER situation_deliveries_no_delete
BEFORE DELETE ON situation_deliveries
BEGIN
    SELECT RAISE(ABORT, 'situation delivery history is immutable');
END;

ALTER TABLE situations ADD COLUMN snooze_cooldown_exempt INTEGER NOT NULL
    DEFAULT 0 CHECK(snooze_cooldown_exempt IN (0, 1));
