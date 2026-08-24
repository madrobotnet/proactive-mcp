"""Consumable non-reply eligibility for daily delivery budget reservation."""

from __future__ import annotations

from json import dumps
from typing import TYPE_CHECKING, Final

from ._situation_sql import SITUATION_JSON

if TYPE_CHECKING:
    from ._situation_models import DeliveryClaim, Situation
    from ._situation_reader import SituationReader

_RESERVED_NON_REPLY_SLOTS: Final[int] = 1
# Only interpolates the shared column projection; values still bind through ?.
_SELECT_CONSUMABLE_NON_REPLY: Final = f"""
            SELECT SUM(_proactive_capture_situation({SITUATION_JSON}))
            FROM (
                SELECT * FROM situations
                WHERE id IN (SELECT value FROM json_each(?))
                  AND state = 'pending'
                  AND priority != 'critical'
                  AND situation_type != 'reply_deadline'
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM situation_type_mutes
                      WHERE situation_type = situations.situation_type
                  )
                  AND (
                      snooze_cooldown_exempt = 1 OR NOT EXISTS (
                          SELECT 1 FROM situation_deliveries
                          WHERE situation_id = situations.id
                            AND delivered_at > ?
                      )
                  )
                  AND (? = 0 OR NOT EXISTS (
                      SELECT 1 FROM situation_delivery_claims
                      WHERE situation_id = situations.id
                  ))
                LIMIT 1
            )
            """  # noqa: S608


def reserved_non_reply_slots_for_claim(
    reader: SituationReader,
    candidates: tuple[Situation, ...],
    claim: DeliveryClaim,
) -> int:
    """Reserve capacity only while a claim-time non-reply can consume it.

    A muted, lapsed, or cooling-down candidate would never survive this
    pass's own write, so holding a budget slot open for it only starves
    the reply deadlines that could have used it.
    """
    if not claim.allow_noncritical or not candidates:
        return 0
    consumable = reader.capture_situations(
        _SELECT_CONSUMABLE_NON_REPLY,
        (
            dumps([candidate.id for candidate in candidates]),
            claim.delivered_at,
            claim.cooldown_after,
            0,
        ),
    )
    return _RESERVED_NON_REPLY_SLOTS if consumable else 0


def reserved_non_reply_slots_for_reservation(
    reader: SituationReader,
    candidates: tuple[Situation, ...],
    claim: DeliveryClaim,
) -> int:
    """Reserve capacity only while a lease-time non-reply can consume it.

    A muted, lapsed, cooling-down, or already-leased candidate would never
    survive this pass's own write, so holding a budget slot open for it
    only starves the reply deadlines that could have used it.
    """
    if not claim.allow_noncritical or not candidates:
        return 0
    consumable = reader.capture_situations(
        _SELECT_CONSUMABLE_NON_REPLY,
        (
            dumps([candidate.id for candidate in candidates]),
            claim.delivered_at,
            claim.cooldown_after,
            1,
        ),
    )
    return _RESERVED_NON_REPLY_SLOTS if consumable else 0
