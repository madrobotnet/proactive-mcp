-- Additive operational state for source generations, situation provenance,
-- collector observations, and daemon run outcomes.
ALTER TABLE source_detection_generations
ADD COLUMN issued_at TEXT;

ALTER TABLE source_detection_generations
ADD COLUMN applied_at TEXT;

ALTER TABLE situations
ADD COLUMN source_name TEXT CHECK(
    source_name IS NULL
    OR source_name IN ('gmail', 'calendar', 'memory', 'local')
);

ALTER TABLE situations
ADD COLUMN source_generation INTEGER CHECK(
    source_generation IS NULL OR source_generation > 0
);

UPDATE situations
SET source_name = CASE situation_type
    WHEN 'reply_deadline' THEN 'gmail'
    WHEN 'calendar_conflict' THEN 'calendar'
    WHEN 'personal_occasion' THEN 'memory'
END;

CREATE TABLE collector_observations (
    profile TEXT PRIMARY KEY NOT NULL
        CHECK(profile IN ('full', 'scheduled')),
    last_check_at TEXT,
    last_confirm_at TEXT,
    CHECK(last_check_at IS NOT NULL OR last_confirm_at IS NULL)
) WITHOUT ROWID;

CREATE TABLE source_operational_state (
    id INTEGER PRIMARY KEY NOT NULL CHECK(id = 1),
    credential_state TEXT NOT NULL CHECK(
        credential_state IN ('unknown', 'available', 'missing', 'unavailable')
    ),
    observed_at TEXT NOT NULL
) WITHOUT ROWID;

ALTER TABLE daemon_status
ADD COLUMN mode TEXT CHECK(mode IS NULL OR mode IN ('once', 'continuous'));

ALTER TABLE daemon_status
ADD COLUMN last_run_state TEXT NOT NULL DEFAULT 'unknown' CHECK(
    last_run_state IN ('unknown', 'succeeded', 'degraded', 'failed')
);

ALTER TABLE daemon_status
ADD COLUMN last_failure_phase TEXT CHECK(
    last_failure_phase IS NULL OR last_failure_phase IN (
        'config', 'database', 'credential', 'source_sync', 'evaluation',
        'notification', 'heartbeat', 'runtime_ownership', 'service'
    )
);

ALTER TABLE daemon_status
ADD COLUMN last_failure_code TEXT CHECK(
    last_failure_code IS NULL OR last_failure_code IN (
        'invalid', 'unsafe_path', 'open_failed', 'unavailable', 'failed',
        'ownership_conflict', 'notify_failed'
    )
);

ALTER TABLE daemon_status
ADD COLUMN last_failure_at TEXT;

ALTER TABLE daemon_status
ADD COLUMN last_completed_at TEXT;

CREATE TRIGGER daemon_failure_shape_insert
BEFORE INSERT ON daemon_status
WHEN (
    NEW.last_run_state = 'failed'
    AND (
        NEW.last_failure_phase IS NULL
        OR NEW.last_failure_code IS NULL
        OR NEW.last_failure_at IS NULL
    )
) OR (
    NEW.last_run_state != 'failed'
    AND (
        NEW.last_failure_phase IS NOT NULL
        OR NEW.last_failure_code IS NOT NULL
        OR NEW.last_failure_at IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'invalid daemon failure state');
END;

CREATE TRIGGER daemon_failure_shape_update
BEFORE UPDATE OF
    last_run_state, last_failure_phase, last_failure_code, last_failure_at
ON daemon_status
WHEN (
    NEW.last_run_state = 'failed'
    AND (
        NEW.last_failure_phase IS NULL
        OR NEW.last_failure_code IS NULL
        OR NEW.last_failure_at IS NULL
    )
) OR (
    NEW.last_run_state != 'failed'
    AND (
        NEW.last_failure_phase IS NOT NULL
        OR NEW.last_failure_code IS NOT NULL
        OR NEW.last_failure_at IS NOT NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'invalid daemon failure state');
END;
