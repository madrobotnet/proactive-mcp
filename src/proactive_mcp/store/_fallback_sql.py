"""SQL statements for one-shot OS notification fallbacks."""

from __future__ import annotations

from typing import Final

from ._situation_sql import SITUATION_JSON

# Every eligibility rule the fallback policy owns, evaluated against committed
# rows so the claim can re-check it inside its own immediate transaction.
_ELIGIBLE: Final = """
                state = 'pending'
                AND priority IN (SELECT value FROM json_each(?))
                AND detected_at <= ?
                AND (expires_at IS NULL OR expires_at > ?)
                AND NOT EXISTS (
                    SELECT 1 FROM situation_type_mutes
                    WHERE situation_type = situations.situation_type
                )
                AND NOT EXISTS (
                    SELECT 1 FROM situation_deliveries
                    WHERE situation_id = situations.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM situation_delivery_claims
                    WHERE situation_id = situations.id
                      AND expires_at > ?
                )
                AND NOT EXISTS (
                    SELECT 1 FROM situation_fallbacks
                    WHERE situation_id = situations.id
                )
            """
_FALLBACK_ORDER: Final = """
                CASE priority
                    WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2
                END ASC, detected_at ASC, id ASC
            """

# The f-strings below only interpolate the private fragments above; every
# caller-provided value still binds through ? placeholders.
SELECT_FALLBACK_CANDIDATES: Final = f"""
            SELECT SUM(_proactive_capture_situation({SITUATION_JSON}))
            FROM (
                SELECT * FROM situations
                WHERE {_ELIGIBLE}
                ORDER BY {_FALLBACK_ORDER}
            )
            """  # noqa: S608
INSERT_FALLBACK_CLAIM: Final = f"""
            INSERT INTO situation_fallbacks (
                situation_id, priority, outcome, claimed_at
            )
            SELECT id, priority, 'claimed', ?
            FROM situations
            WHERE id = ? AND {_ELIGIBLE}
            """  # noqa: S608
RECORD_FALLBACK_OUTCOME: Final = """
            UPDATE situation_fallbacks
            SET outcome = ?, failure_code = ?, completed_at = ?
            WHERE situation_id = ? AND outcome = 'claimed'
            """
SELECT_FALLBACK_RECORD: Final = """
            SELECT SUM(_proactive_capture_fallback_record(json_object(
                'situation_id', situation_id, 'priority', priority,
                'outcome', outcome, 'failure_code', failure_code,
                'claimed_at', claimed_at, 'completed_at', completed_at
            )))
            FROM situation_fallbacks WHERE situation_id = ?
            """
SELECT_FALLBACK_SUMMARY: Final = """
            SELECT _proactive_capture_fallback_summary(json_object(
                'claimed', (
                    SELECT COUNT(*) FROM situation_fallbacks
                    WHERE outcome = 'claimed'
                ),
                'sent', (
                    SELECT COUNT(*) FROM situation_fallbacks
                    WHERE outcome = 'sent'
                ),
                'failed', (
                    SELECT COUNT(*) FROM situation_fallbacks
                    WHERE outcome = 'failed'
                ),
                'failure_codes', json((
                    SELECT COALESCE(json_group_array(code), json_array())
                    FROM (
                        SELECT DISTINCT failure_code AS code
                        FROM situation_fallbacks
                        WHERE failure_code IS NOT NULL
                        ORDER BY failure_code ASC
                    )
                ))
            ))
            """
