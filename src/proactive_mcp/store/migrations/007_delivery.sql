-- M4 delivery: daemon heartbeat liveness and one-shot OS notification fallbacks.
CREATE TABLE daemon_status (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    state TEXT NOT NULL CHECK(state IN ('running', 'stopped')),
    pid INTEGER NOT NULL CHECK(pid > 0),
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    cycle_count INTEGER NOT NULL DEFAULT 0 CHECK(cycle_count >= 0)
);

CREATE TABLE situation_fallbacks (
    id INTEGER PRIMARY KEY,
    situation_id INTEGER NOT NULL UNIQUE REFERENCES situations(id),
    priority TEXT NOT NULL CHECK(priority IN ('critical', 'high', 'routine')),
    outcome TEXT NOT NULL CHECK(outcome IN ('claimed', 'sent', 'failed')),
    failure_code TEXT CHECK(
        failure_code IN (
            'unsupported_platform', 'tool_missing', 'nonzero_exit', 'timeout',
            'unknown'
        )
    ),
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK((outcome = 'claimed') = (completed_at IS NULL)),
    CHECK((outcome = 'failed') = (failure_code IS NOT NULL))
);

-- A claim row is written before the notification subprocess runs, so exactly one
-- terminal outcome may ever replace it and no claim fact may be rewritten.
CREATE TRIGGER situation_fallbacks_claim_is_immutable
BEFORE UPDATE ON situation_fallbacks
WHEN OLD.outcome != 'claimed'
    OR NEW.outcome NOT IN ('sent', 'failed')
    OR NEW.situation_id != OLD.situation_id
    OR NEW.priority != OLD.priority
    OR NEW.claimed_at != OLD.claimed_at
BEGIN
    SELECT RAISE(ABORT, 'situation fallback history is immutable');
END;

CREATE TRIGGER situation_fallbacks_no_delete
BEFORE DELETE ON situation_fallbacks
BEGIN
    SELECT RAISE(ABORT, 'situation fallback history is immutable');
END;
