"""SQL statements for memory items and entities."""

from __future__ import annotations

from typing import Final

LAST_INSERT_ROWID: Final = "SELECT last_insert_rowid()"
SELECT_ENTITY_ID_BY_ALIAS: Final = (
    "SELECT entity_id FROM entity_aliases WHERE alias_norm = ?"
)
UPDATE_MEMORY_TIMESTAMP: Final = "UPDATE memory_items SET updated_at = ? WHERE id = ?"
INSERT_MEMORY_ITEM: Final = """
            INSERT INTO memory_items (
                kind, entity_id, attribute, content, date_anchor, recurrence, lead_days,
                source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
UPDATE_MEMORY_ITEM: Final = """
            UPDATE memory_items
            SET kind = ?, entity_id = ?, attribute = ?, content = ?, date_anchor = ?,
                recurrence = ?, lead_days = ?, source = ?, updated_at = ?
            WHERE id = ?
            """
ARCHIVE_MEMORY_ITEM: Final = """
            UPDATE memory_items
            SET archived = 1, updated_at = ?
            WHERE id = ? AND archived = 0
            """
INSERT_ENTITY: Final = """
            INSERT INTO entities (kind, path, label, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """
INSERT_ENTITY_ALIAS: Final = """
                INSERT INTO entity_aliases (
                    entity_id, alias, alias_norm, source, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """
SELECT_DATED_DUPLICATE: Final = """
                SELECT id FROM memory_items
                WHERE entity_id = ? AND attribute = ? AND date_anchor = ?
                  AND archived = 0
                """
SELECT_DATED_DUPLICATE_EXCLUDING: Final = """
            SELECT id FROM memory_items
            WHERE entity_id = ? AND attribute = ? AND date_anchor = ? AND archived = 0
              AND id != ?
            """
SELECT_MEMORY_BY_ID: Final = """
            SELECT SUM(_proactive_capture_memory_item(
                json_object(
                    'id', m.id, 'kind', m.kind, 'entity_id', m.entity_id,
                    'entity', e.label, 'entity_kind', e.kind,
                    'entity_path', e.path, 'attribute', m.attribute,
                    'content', m.content, 'date_anchor', m.date_anchor,
                    'recurrence', m.recurrence, 'lead_days', m.lead_days,
                    'source', m.source, 'created_at', m.created_at,
                    'updated_at', m.updated_at, 'archived', m.archived,
                    'is_contradictory', EXISTS (
                        SELECT 1 FROM memory_items AS other
                        WHERE other.entity_id = m.entity_id
                          AND other.attribute = m.attribute
                          AND other.date_anchor != m.date_anchor
                          AND other.archived = 0
                          AND other.attribute != 'free'
                    )
                )
            ))
            FROM memory_items AS m
            LEFT JOIN entities AS e ON e.id = m.entity_id
            WHERE m.id = ?
            """
SELECT_ENTITY_BY_ID: Final = """
            SELECT SUM(_proactive_capture_entity(
                json_object(
                    'id', id, 'kind', kind, 'path', path, 'label', label,
                    'status', status, 'created_at', created_at, 'updated_at', updated_at
                )
            ))
            FROM (SELECT * FROM entities WHERE id = ?)
            """
SELECT_RECALL_MEMORY_ITEMS: Final = """
            SELECT SUM(_proactive_capture_memory_item(
                json_object(
                    'id', m.id, 'kind', m.kind, 'entity_id', m.entity_id,
                    'entity', e.label, 'entity_kind', e.kind,
                    'entity_path', e.path, 'attribute', m.attribute,
                    'content', m.content, 'date_anchor', m.date_anchor,
                    'recurrence', m.recurrence, 'lead_days', m.lead_days,
                    'source', m.source, 'created_at', m.created_at,
                    'updated_at', m.updated_at, 'archived', m.archived,
                    'is_contradictory', EXISTS (
                        SELECT 1 FROM memory_items AS other
                        WHERE other.entity_id = m.entity_id
                          AND other.attribute = m.attribute
                          AND other.date_anchor != m.date_anchor
                          AND other.archived = 0
                          AND other.attribute != 'free'
                    )
                )
            ))
            FROM (
                SELECT m.*, e.label, e.kind AS entity_kind, e.path
                FROM memory_items AS m
                LEFT JOIN entities AS e ON e.id = m.entity_id
                WHERE m.archived = 0
                  AND (
                    e.label LIKE ? ESCAPE '\\'
                    OR e.path LIKE ? ESCAPE '\\'
                    OR m.content LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1 FROM entity_aliases AS a
                        WHERE a.entity_id = e.id AND a.alias LIKE ? ESCAPE '\\'
                    )
                  )
                  AND (? IS NULL OR m.kind = ?)
                  AND (? IS NULL OR e.kind = ?)
                  AND (? IS NULL OR e.path = ? OR e.path LIKE ? ESCAPE '\\')
                ORDER BY m.updated_at DESC, m.id DESC
                LIMIT ?
            ) AS m
            LEFT JOIN entities AS e ON e.id = m.entity_id
            """
SELECT_ACTIVE_ENTITIES: Final = """
            SELECT SUM(_proactive_capture_entity(
                json_object(
                    'id', id, 'kind', kind, 'path', path, 'label', label,
                    'status', status, 'created_at', created_at,
                    'updated_at', updated_at
                )
            ))
            FROM (
                SELECT * FROM entities
                WHERE status = 'active'
                  AND (? IS NULL OR kind = ?)
                  AND (? IS NULL OR path = ? OR path LIKE ? ESCAPE '\\')
                ORDER BY kind ASC, path ASC, label ASC, id ASC
            )
            """
