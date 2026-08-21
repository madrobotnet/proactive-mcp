from __future__ import annotations

from datetime import UTC, timedelta

import pytest

from proactive_mcp import situations
from tests.situation_test_support import require_m3, utc_datetime


def test_reply_deadline_detects_elapsed_external_message_after_threshold() -> None:
    # Given: an inbox thread whose latest message came from another person.
    require_m3("InboxThreadSnapshot", "detect_reply_deadlines")
    now = utc_datetime(2026, 8, 21, 12)
    thread = situations.InboxThreadSnapshot(
        thread_id="thread-7",
        latest_message_id="message-9",
        latest_from_user=False,
        user_is_recipient=True,
        latest_message_at=now - timedelta(hours=48),
        subject="Quarterly review",
        sender_display="Fixture Sender",
        snippet="Please review the attached notes.",
    )

    # When: the deterministic detector evaluates the threshold boundary.
    detected = situations.detect_reply_deadlines(threads=(thread,), now=now, tz=UTC)

    # Then: one routine detection uses thread plus latest-message identity.
    assert len(detected) == 1
    candidate = detected[0]
    assert candidate.situation_type == "reply_deadline"
    assert candidate.priority == "routine"
    assert candidate.dedupe_key == "reply_deadline:thread-7:message-9"
    assert candidate.evidence.facts["thread_id"] == "thread-7"
    assert candidate.evidence.facts["latest_message_id"] == "message-9"
    assert candidate.evidence.quoted_external == {
        "subject": "Quarterly review",
        "sender": "Fixture Sender",
    }


def test_reply_deadline_requires_external_latest_message_and_user_recipient() -> None:
    # Given: old threads that do not require a user reply.
    require_m3("InboxThreadSnapshot", "detect_reply_deadlines")
    now = utc_datetime(2026, 8, 21, 12)
    threads = (
        situations.InboxThreadSnapshot(
            thread_id="self-latest",
            latest_message_id="m1",
            latest_from_user=True,
            user_is_recipient=True,
            latest_message_at=now - timedelta(hours=49),
        ),
        situations.InboxThreadSnapshot(
            thread_id="not-to-user",
            latest_message_id="m2",
            latest_from_user=False,
            user_is_recipient=False,
            latest_message_at=now - timedelta(hours=49),
        ),
    )

    # When: the detector evaluates both non-replyable classes.
    detected = situations.detect_reply_deadlines(threads=threads, now=now, tz=UTC)

    # Then: neither creates a situation.
    assert detected == ()


@pytest.mark.parametrize(
    ("deadline_text", "expected_date"),
    [
        ("Deadline: 2026-08-22.", "2026-08-22"),
        ("8월 22일까지 보내 주세요.", "2026-08-22"),
    ],
)
def test_reply_deadline_detects_english_and_korean_dates_before_age_threshold(
    deadline_text: str,
    expected_date: str,
) -> None:
    # Given: a recent external message with an imminent explicit deadline.
    require_m3("InboxThreadSnapshot", "detect_reply_deadlines")
    now = utc_datetime(2026, 8, 21, 12)
    thread = situations.InboxThreadSnapshot(
        thread_id="dated-thread",
        latest_message_id="dated-message",
        latest_from_user=False,
        user_is_recipient=True,
        latest_message_at=now - timedelta(hours=2),
        subject="Delivery date",
        snippet=deadline_text,
    )

    # When: conservative deadline patterns are evaluated.
    detected = situations.detect_reply_deadlines(threads=(thread,), now=now, tz=UTC)

    # Then: structured evidence carries the date and priority is high.
    assert len(detected) == 1
    assert detected[0].priority == "high"
    assert detected[0].evidence.facts["deadline_date"] == expected_date


def test_reply_deadline_changes_dedupe_identity_with_latest_message() -> None:
    # Given: two snapshots of one thread with different latest messages.
    require_m3("InboxThreadSnapshot", "detect_reply_deadlines")
    now = utc_datetime(2026, 8, 21, 12)
    snapshots = tuple(
        situations.InboxThreadSnapshot(
            thread_id="same-thread",
            latest_message_id=message_id,
            latest_from_user=False,
            user_is_recipient=True,
            latest_message_at=now - timedelta(hours=49),
        )
        for message_id in ("old-message", "new-message")
    )

    # When: each source version is detected.
    detected = situations.detect_reply_deadlines(
        threads=snapshots,
        now=now,
        tz=UTC,
    )

    # Then: dedupe identity follows the latest message, not just the thread.
    assert {candidate.dedupe_key for candidate in detected} == {
        "reply_deadline:same-thread:old-message",
        "reply_deadline:same-thread:new-message",
    }
