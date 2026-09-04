-- Canonical identity and dedupe for active free dated memories.
ALTER TABLE memory_items
ADD COLUMN content_norm TEXT;

UPDATE memory_items
SET content_norm = _proactive_memory_content_norm(content);

UPDATE memory_items AS keeper
SET updated_at = (
    SELECT MAX(member.updated_at)
    FROM memory_items AS member
    WHERE member.archived = 0
      AND member.attribute = 'free'
      AND member.date_anchor IS NOT NULL
      AND member.kind = keeper.kind
      AND COALESCE(member.entity_id, 0) = COALESCE(keeper.entity_id, 0)
      AND member.date_anchor = keeper.date_anchor
      AND member.recurrence = keeper.recurrence
      AND member.content_norm = keeper.content_norm
)
WHERE keeper.archived = 0
  AND keeper.attribute = 'free'
  AND keeper.date_anchor IS NOT NULL
  AND keeper.id = (
      SELECT MIN(member.id)
      FROM memory_items AS member
      WHERE member.archived = 0
        AND member.attribute = 'free'
        AND member.date_anchor IS NOT NULL
        AND member.kind = keeper.kind
        AND COALESCE(member.entity_id, 0) = COALESCE(keeper.entity_id, 0)
        AND member.date_anchor = keeper.date_anchor
        AND member.recurrence = keeper.recurrence
        AND member.content_norm = keeper.content_norm
  );

UPDATE memory_items AS loser
SET archived = 1
WHERE loser.archived = 0
  AND loser.attribute = 'free'
  AND loser.date_anchor IS NOT NULL
  AND loser.id != (
      SELECT MIN(keeper.id)
      FROM memory_items AS keeper
      WHERE keeper.archived = 0
        AND keeper.attribute = 'free'
        AND keeper.date_anchor IS NOT NULL
        AND keeper.kind = loser.kind
        AND COALESCE(keeper.entity_id, 0) = COALESCE(loser.entity_id, 0)
        AND keeper.date_anchor = loser.date_anchor
        AND keeper.recurrence = loser.recurrence
        AND keeper.content_norm = loser.content_norm
  );

CREATE UNIQUE INDEX uq_memory_free_dated
    ON memory_items(
        kind,
        COALESCE(entity_id, 0),
        date_anchor,
        recurrence,
        content_norm
    )
    WHERE archived = 0
      AND attribute = 'free'
      AND date_anchor IS NOT NULL;
