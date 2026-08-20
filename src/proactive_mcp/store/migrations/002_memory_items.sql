-- M1 memory items.
CREATE TABLE memory_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    entity TEXT,
    content TEXT NOT NULL,
    date_anchor TEXT,
    recurrence TEXT NOT NULL DEFAULT 'none',
    lead_days INTEGER,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
