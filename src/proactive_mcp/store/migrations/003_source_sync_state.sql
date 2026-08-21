-- M2 source synchronization freshness state.
CREATE TABLE source_sync_state (
    source TEXT PRIMARY KEY NOT NULL,
    auth_state TEXT NOT NULL,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error_code TEXT,
    sync_cursor TEXT,
    updated_at TEXT NOT NULL
);
