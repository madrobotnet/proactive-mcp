-- M2.5 memory model v2: entities, aliases, and reconstructed memory_items.
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('person','place','org','thing','activity')),
    path TEXT,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','merged','archived')),
    merged_into INTEGER REFERENCES entities(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_entities_kind_path ON entities(kind, path);
CREATE INDEX idx_entities_path ON entities(path);

CREATE TABLE entity_aliases (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_entity_alias_norm ON entity_aliases(alias_norm);

-- Legacy entity spellings collapse under the runtime alias key
-- (Unicode NFC + casefold + all whitespace removed). One deterministic entity
-- and one representative alias are kept per normalized key, and every legacy
-- memory row still joins back to that representative while preserving its own
-- content, date, source, and timestamps.
INSERT INTO entities (kind, path, label, status, created_at, updated_at)
SELECT
    'person',
    NULL,
    MIN(_proactive_normalize_label(entity)),
    'active',
    MIN(created_at),
    MAX(updated_at)
FROM memory_items
WHERE entity IS NOT NULL AND _proactive_alias_norm(entity) != ''
GROUP BY _proactive_alias_norm(entity);

INSERT INTO entity_aliases (entity_id, alias, alias_norm, source, created_at)
SELECT
    e.id,
    e.label,
    _proactive_alias_norm(e.label),
    (
        SELECT m.source
        FROM memory_items AS m
        WHERE m.entity IS NOT NULL
          AND _proactive_alias_norm(m.entity) != ''
          AND _proactive_alias_norm(m.entity) = _proactive_alias_norm(e.label)
        ORDER BY m.id
        LIMIT 1
    ),
    e.created_at
FROM entities AS e;

CREATE TABLE memory_items_new (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('fact','commitment','preference','note')),
    entity_id INTEGER REFERENCES entities(id),
    attribute TEXT NOT NULL DEFAULT 'free'
        CHECK(attribute IN ('birthday','anniversary','deadline','relationship','free')),
    supersedes_id INTEGER REFERENCES memory_items_new(id),
    content TEXT NOT NULL,
    date_anchor TEXT,
    recurrence TEXT NOT NULL DEFAULT 'none',
    lead_days INTEGER,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);

INSERT INTO memory_items_new (
    id,
    kind,
    entity_id,
    attribute,
    supersedes_id,
    content,
    date_anchor,
    recurrence,
    lead_days,
    source,
    created_at,
    updated_at,
    archived
)
SELECT
    m.id,
    CASE m.kind WHEN 'person_fact' THEN 'fact' ELSE m.kind END,
    e.id,
    'free',
    NULL,
    m.content,
    m.date_anchor,
    m.recurrence,
    m.lead_days,
    m.source,
    m.created_at,
    m.updated_at,
    m.archived
FROM memory_items AS m
LEFT JOIN entities AS e
    ON m.entity IS NOT NULL
   AND _proactive_alias_norm(m.entity) != ''
   AND _proactive_alias_norm(m.entity) = _proactive_alias_norm(e.label);

DROP TABLE memory_items;
ALTER TABLE memory_items_new RENAME TO memory_items;

CREATE UNIQUE INDEX uq_memory_dated_fact
    ON memory_items(entity_id, attribute, date_anchor)
    WHERE archived = 0 AND attribute <> 'free';
