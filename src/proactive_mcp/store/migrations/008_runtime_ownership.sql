-- Runtime ownership fences for daemon liveness and degraded lazy synchronization.
ALTER TABLE daemon_status
ADD COLUMN owner_token TEXT;

ALTER TABLE daemon_status
ADD COLUMN poll_interval_seconds REAL
CHECK(poll_interval_seconds IS NULL OR poll_interval_seconds > 0);

-- Lazy source reads cover Gmail and Calendar as one remote attempt, so one
-- singleton lease serializes that operation across processes and connections.
CREATE TABLE lazy_sync_lease (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    owner_token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    CHECK(expires_at > acquired_at)
);
