"""Deterministic reply-deadline detection over inbox thread snapshots."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from proactive_mcp.store import Detection, SituationEvidence

from ._deadline_text import DeadlineScan, scan_deadline_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime, tzinfo

    from proactive_mcp.store import SituationPriority

    from .inputs import InboxThreadSnapshot

__all__ = ["DEFAULT_REPLY_THRESHOLD", "detect_reply_deadlines"]

DEFAULT_REPLY_THRESHOLD: Final = timedelta(hours=48)
_HIGH_PRIORITY_MAX_DAYS: Final = 1
_SECONDS_PER_HOUR: Final = 3600


def detect_reply_deadlines(
    threads: Sequence[InboxThreadSnapshot],
    *,
    now: datetime,
    tz: tzinfo,
    threshold: timedelta = DEFAULT_REPLY_THRESHOLD,
) -> tuple[Detection, ...]:
    """Detect threads that await the user's reply, per product plan §6.1.

    A thread triggers when its latest message came from the counterpart,
    the user is a recipient, and either the message aged past the threshold
    or its subject, full body, or snippet holds conservative deadline language. The
    detection resolves naturally once the user replies, because the latest
    message is then no longer from the counterpart.
    """
    today = now.astimezone(tz).date()
    detections: list[Detection] = []
    for thread in threads:
        if thread.latest_from_user or not thread.user_is_recipient:
            continue
        age = now - thread.latest_message_at
        text = "\n".join(
            part
            for part in (thread.subject, thread.body_text, thread.snippet)
            if part is not None
        )
        scan = scan_deadline_text(text, today=today)
        if age < threshold and not scan.has_marker:
            continue
        detections.append(
            _detection(thread, age=age, threshold=threshold, scan=scan, today=today)
        )
    return tuple(detections)


def _detection(
    thread: InboxThreadSnapshot,
    *,
    age: timedelta,
    threshold: timedelta,
    scan: DeadlineScan,
    today: date,
) -> Detection:
    age_hours = int(age.total_seconds() // _SECONDS_PER_HOUR)
    priority = _priority(scan, today)
    reasons: list[str] = []
    if age >= threshold:
        threshold_hours = int(threshold.total_seconds() // _SECONDS_PER_HOUR)
        reasons.append(f"no reply for {age_hours}h (threshold {threshold_hours}h)")
    if scan.has_marker:
        suffix = (
            f" for {scan.deadline_date.isoformat()}"
            if scan.deadline_date is not None
            else ""
        )
        reasons.append(f"deadline language detected{suffix}")
    title = (
        f"Reply needed by {scan.deadline_date.isoformat()}"
        if scan.deadline_date is not None
        else f"Reply pending for {age_hours}h"
    )
    facts = {
        "thread_id": thread.thread_id,
        "latest_message_id": thread.latest_message_id,
        "age_hours": str(age_hours),
    }
    if scan.deadline_date is not None:
        facts["deadline_date"] = scan.deadline_date.isoformat()
    quoted: dict[str, str] = {}
    if thread.subject is not None:
        quoted["subject"] = thread.subject
    if thread.sender_display is not None:
        quoted["sender"] = thread.sender_display
    if scan.matched_marker is not None:
        quoted["matched_deadline_text"] = scan.matched_marker
    return Detection(
        situation_type="reply_deadline",
        dedupe_key=f"reply_deadline:{thread.thread_id}:{thread.latest_message_id}",
        priority=priority,
        title=title,
        why_now="; ".join(reasons).capitalize(),
        evidence=SituationEvidence(facts=facts, quoted_external=quoted),
    )


def _priority(scan: DeadlineScan, today: date) -> SituationPriority:
    if scan.deadline_date is None:
        return "routine"
    days_until = (scan.deadline_date - today).days
    return "high" if days_until <= _HIGH_PRIORITY_MAX_DAYS else "routine"
