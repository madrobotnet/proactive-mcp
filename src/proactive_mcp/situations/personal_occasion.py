"""Deterministic memory-based occasion detection, per product plan §6.3."""

from __future__ import annotations

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
_TITLE_CONTENT_LIMIT: Final = 40


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
    A group triggers once its earliest upcoming occurrence is at most
    ``lead_days`` away.
    """
    today = now.astimezone(tz).date()
    groups: dict[tuple[str, int, str], list[_Occurrence]] = {}
    for item in items:
        occurrence = _next_occurrence(item, today)
        if occurrence is None:
            continue
        groups.setdefault(_group_key(item), []).append(
            _Occurrence(item=item, occurrence=occurrence)
        )
    detections: list[Detection] = []
    for rows in groups.values():
        detection = _group_detection(
            rows,
            today=today,
            tz=tz,
            default_lead_days=default_lead_days,
        )
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
    trigger = min(rows, key=lambda row: (row.occurrence, row.item.id))
    lead_days = (
        trigger.item.lead_days
        if trigger.item.lead_days is not None
        else default_lead_days
    )
    days_until = (trigger.occurrence - today).days
    if days_until > lead_days:
        return None
    anchors = sorted(
        {row.item.date_anchor for row in rows if row.item.date_anchor is not None}
    )
    contradictory = len(anchors) > 1
    item = trigger.item
    occurrence_iso = trigger.occurrence.isoformat()
    why_now = f"D-{days_until}: {item.content} on {occurrence_iso}"
    if contradictory:
        why_now += f" - contradictory dates on record: {', '.join(anchors)}"
    return Detection(
        situation_type="personal_occasion",
        dedupe_key=_dedupe_key(item, trigger.occurrence.year),
        priority="high",
        title=_title(item),
        why_now=why_now,
        evidence=SituationEvidence(
            facts={
                "memory_ids": ",".join(
                    str(row.item.id) for row in sorted(rows, key=lambda r: r.item.id)
                ),
                "occurrence": occurrence_iso,
                "days_until": str(days_until),
                "lead_days": str(lead_days),
                "content": item.content,
            },
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


def _title(item: MemoryItem) -> str:
    label = "date" if item.attribute == "free" else item.attribute
    if item.entity is not None:
        return f"Upcoming {label}: {item.entity}"
    content = item.content
    if len(content) > _TITLE_CONTENT_LIMIT:
        content = content[: _TITLE_CONTENT_LIMIT - 1] + "…"
    return f"Upcoming {label}: {content}"
