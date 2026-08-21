"""SQL statements for situations and their state machine."""

from __future__ import annotations

from typing import Final

_SITUATION_JSON: Final = """
            json_object(
                'id', id, 'situation_type', situation_type,
                'dedupe_key', dedupe_key, 'state', state, 'priority', priority,
                'title', title, 'why_now', why_now, 'evidence', json(evidence),
                'expires_at', expires_at, 'detected_at', detected_at,
                'updated_at', updated_at, 'delivered_at', delivered_at,
                'acknowledged_at', acknowledged_at, 'snoozed_until', snoozed_until,
                'resolved_at', resolved_at, 'expired_at', expired_at,
                'muted_at', muted_at
            )
            """

INSERT_SITUATION: Final = """
            INSERT INTO situations (
                situation_type, dedupe_key, state, priority, title, why_now,
                evidence, expires_at, detected_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """
REFRESH_SITUATION: Final = """
            UPDATE situations
            SET priority = ?, title = ?, why_now = ?, evidence = ?,
                expires_at = ?, updated_at = ?
            WHERE id = ?
            """
REACTIVATE_SITUATION: Final = """
            UPDATE situations
            SET state = 'pending', priority = ?, title = ?, why_now = ?,
                evidence = ?, expires_at = ?, detected_at = ?, updated_at = ?,
                resolved_at = NULL, expired_at = NULL
            WHERE id = ?
            """
MARK_DELIVERED: Final = """
            UPDATE situations
            SET state = 'delivered', delivered_at = ?, snoozed_until = NULL,
                updated_at = ?
            WHERE id = ? AND state = 'pending'
            """
ACKNOWLEDGE_SITUATION: Final = """
            UPDATE situations
            SET state = 'acknowledged', acknowledged_at = ?, updated_at = ?
            WHERE id = ? AND state = 'delivered'
            """
SNOOZE_SITUATION: Final = """
            UPDATE situations
            SET state = 'snoozed', snoozed_until = ?, updated_at = ?
            WHERE id = ? AND state = 'delivered'
            """
MUTE_SITUATION: Final = """
            UPDATE situations
            SET state = 'muted', muted_at = ?, updated_at = ?
            WHERE id = ? AND state = 'delivered'
            """
RESOLVE_SITUATION: Final = """
            UPDATE situations
            SET state = 'resolved', resolved_at = ?, updated_at = ?
            WHERE id = ? AND state IN ('pending', 'delivered')
            """
WAKE_SNOOZED: Final = """
            UPDATE situations
            SET state = 'pending', updated_at = ?
            WHERE state = 'snoozed' AND snoozed_until IS NOT NULL
              AND snoozed_until <= ?
            """
EXPIRE_LAPSED: Final = """
            UPDATE situations
            SET state = 'expired', expired_at = ?, updated_at = ?
            WHERE state = 'delivered'
              AND expires_at IS NOT NULL AND expires_at <= ?
            """
INSERT_TYPE_MUTE: Final = """
            INSERT INTO situation_type_mutes (situation_type, created_at)
            VALUES (?, ?)
            ON CONFLICT(situation_type) DO NOTHING
            """
COUNT_DELIVERED_BETWEEN: Final = """
            SELECT COUNT(*) FROM situations
            WHERE delivered_at IS NOT NULL
              AND delivered_at >= ? AND delivered_at < ?
              AND priority != 'critical'
            """
# The f-strings below only interpolate the private column projection above;
# every user-provided value still binds through ? placeholders.
SELECT_SITUATION_BY_ID: Final = f"""
            SELECT SUM(_proactive_capture_situation({_SITUATION_JSON}))
            FROM situations WHERE id = ?
            """  # noqa: S608
SELECT_SITUATION_BY_DEDUPE_KEY: Final = f"""
            SELECT SUM(_proactive_capture_situation({_SITUATION_JSON}))
            FROM situations WHERE dedupe_key = ?
            """  # noqa: S608
SELECT_SITUATIONS: Final = f"""
            SELECT SUM(_proactive_capture_situation({_SITUATION_JSON}))
            FROM (
                SELECT * FROM situations
                WHERE (? IS NULL OR state = ?)
                ORDER BY detected_at ASC, id ASC
            )
            """  # noqa: S608
SELECT_ACTIVE_BY_TYPE: Final = f"""
            SELECT SUM(_proactive_capture_situation({_SITUATION_JSON}))
            FROM (
                SELECT * FROM situations
                WHERE situation_type = ?
                  AND state IN ('pending', 'delivered')
                ORDER BY id ASC
            )
            """  # noqa: S608
SELECT_MUTED_TYPES: Final = """
            SELECT SUM(_proactive_capture_situation_str(situation_type))
            FROM (
                SELECT situation_type FROM situation_type_mutes
                ORDER BY situation_type ASC
            )
            """
