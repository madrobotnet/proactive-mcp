"""Deterministic memory-based occasion detection, per product plan §6.3."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from proactive_mcp.store import Detection, SituationEvidence

from ._dates import local_day_end, parse_anchor, yearly_occurrence_on_or_after

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, tzinfo

    from proactive_mcp.store import MemoryItem

__all__ = ["DEFAULT_LEAD_DAYS", "detect_personal_occasions"]

DEFAULT_LEAD_DAYS: Final = 7
_LOGGER = logging.getLogger(__name__)
_INVALID_MEMORY_WARNING = "skipped invalid dated memory during occasion evaluation"


@dataclass(frozen=True, slots=True)
class _Occurrence:
    """One dated memory row with its next occurrence."""

    item: MemoryItem
    occurrence: date


def detect_personal_occasions(
    items: Sequence[MemoryItem],
    *,
    now: datetime,
    tz: tzinfo,
    default_lead_days: int = DEFAULT_LEAD_DAYS,
) -> tuple[Detection, ...]:
    """Detect upcoming memory-based occasions, per product plan §6.3.

    Rows that share an entity and a dated attribute collapse into one
    occasion per occurrence year, so duplicate memories never notify twice
    (Owner decision on issue #4). Contradictory anchors within such a group
    still notify once, with every recorded date exposed in the evidence.
    Each row becomes eligible against its own effective lead window, then
    the earliest eligible occurrence supplies the single group detection.
    """
    today = now.astimezone(tz).date()
    groups: dict[tuple[str, int, str], list[_Occurrence]] = {}
    for item in items:
        try:
            occurrence = _next_occurrence(item, today)
        except (OverflowError, ValueError):
            _LOGGER.warning(_INVALID_MEMORY_WARNING)
            continue
        if occurrence is None:
            continue
        groups.setdefault(_group_key(item), []).append(
            _Occurrence(item=item, occurrence=occurrence)
        )
    detections: list[Detection] = []
    for rows in groups.values():
        try:
            detection = _group_detection(
                rows,
                today=today,
                tz=tz,
                default_lead_days=default_lead_days,
            )
        except (OverflowError, ValueError):
            _LOGGER.warning(_INVALID_MEMORY_WARNING)
            continue
        if detection is not None:
            detections.append(detection)
    return tuple(detections)


def _group_key(item: MemoryItem) -> tuple[str, int, str]:
    if item.entity_id is not None and item.attribute != "free":
        return ("entity", item.entity_id, item.attribute)
    return ("item", item.id, "")


def _next_occurrence(item: MemoryItem, today: date) -> date | None:
    if item.date_anchor is None:
        return None
    parsed = parse_anchor(item.date_anchor)
    if parsed is None:
        return None
    year, month, day = parsed
    if year is None or item.recurrence == "yearly":
        return yearly_occurrence_on_or_after(today, month, day)
    occurrence = date(year, month, day)
    return occurrence if occurrence >= today else None


def _group_detection(
    rows: Sequence[_Occurrence],
    *,
    today: date,
    tz: tzinfo,
    default_lead_days: int,
) -> Detection | None:
    eligible = tuple(
        row
        for row in rows
        if (row.occurrence - today).days
        <= (row.item.lead_days if row.item.lead_days is not None else default_lead_days)
    )
    if not eligible:
        return None
    trigger = min(eligible, key=lambda row: (row.occurrence, row.item.id))
    lead_days = (
        trigger.item.lead_days
        if trigger.item.lead_days is not None
        else default_lead_days
    )
    days_until = (trigger.occurrence - today).days
    anchors = sorted(
        {row.item.date_anchor for row in rows if row.item.date_anchor is not None}
    )
    contradictory = len(anchors) > 1
    item = trigger.item
    occurrence_iso = trigger.occurrence.isoformat()
    label = "saved date" if item.attribute == "free" else item.attribute
    why_now = f"D-{days_until}: upcoming {label} on {occurrence_iso}"
    if contradictory:
        why_now += f" - contradictory dates on record: {', '.join(anchors)}"
    return Detection(
        situation_type="personal_occasion",
        dedupe_key=_dedupe_key(item, trigger.occurrence.year),
        priority="high",
        title=f"Upcoming {label}",
        why_now=why_now,
        evidence=SituationEvidence(
            facts={
                "selected_memory_id": str(item.id),
                "memory_ids": ",".join(
                    str(row.item.id) for row in sorted(rows, key=lambda r: r.item.id)
                ),
                "occurrence": occurrence_iso,
                "days_until": str(days_until),
                "lead_days": str(lead_days),
                "memory_source": item.source,
            },
            quoted_memory=_memory_quotes(item),
            contradictory_dates=tuple(anchors) if contradictory else (),
        ),
        expires_at=local_day_end(trigger.occurrence, tz),
    )


def _dedupe_key(item: MemoryItem, occurrence_year: int) -> str:
    if item.entity_id is not None and item.attribute != "free":
        return (
            f"personal_occasion:entity:{item.entity_id}"
            f":{item.attribute}:{occurrence_year}"
        )
    return f"personal_occasion:item:{item.id}:{occurrence_year}"


def _memory_quotes(item: MemoryItem) -> dict[str, str]:
    values = {"content": item.content}
    if item.entity is not None:
        values["entity"] = item.entity
    if item.entity_path is not None:
        values["entity_path"] = item.entity_path
    return values
