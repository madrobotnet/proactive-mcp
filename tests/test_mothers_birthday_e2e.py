"""Mother's Birthday acceptance narrative for M4 delivery (product plan §11.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from proactive_mcp.delivery.daemon import DaemonDependencies, WatcherDaemon
from proactive_mcp.store import NewMemory
from tests.daemon_test_support import RecordingNotifier
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import open_harness

if TYPE_CHECKING:
    from pathlib import Path

_MENTIONED = utc_datetime(2026, 3, 2, 9)
_LEAD_MORNING = utc_datetime(2026, 7, 11, 9)
_NEXT_LEAD_MORNING = utc_datetime(2027, 7, 11, 9)
_WATCHER_PID: Final = 4242


def test_the_mothers_birthday_reaches_the_user_once_every_year(
    tmp_path: Path,
) -> None:
    # Given: the user once mentioned 엄마's birthday while talking to an agent.
    with open_harness(tmp_path, _MENTIONED) as harness:
        remembered = harness.store.remember(
            NewMemory(
                kind="fact",
                entity="엄마",
                entity_kind="person",
                entity_path="가족/어머니",
                attribute="birthday",
                content="엄마 생신",
                date_anchor="--07-18",
                recurrence="yearly",
                lead_days=7,
            )
        )

        # Then: 엄마 is an alias of the canonical 가족/어머니 entity.
        recalled = harness.store.recall("어머니")
        assert remembered.entity == "엄마"
        assert remembered.entity_path == "가족/어머니"
        assert tuple(item.id for item in recalled) == (remembered.id,)

        # When: the watcher daemon runs its pass on the D-7 morning.
        harness.clock.set(_LEAD_MORNING)
        watched = WatcherDaemon(
            DaemonDependencies(
                pid=_WATCHER_PID,
                clock=harness.clock,
                heartbeat=harness.store.daemon,
                evaluation=harness.dependencies.evaluation,
                notifier=RecordingNotifier(),
            )
        ).run_once()
        detected = harness.store.situations.list_situations()

        # Then: the occasion is detected, and the daemon delivers nothing (§5.1).
        assert watched.evaluation.result.created == 1
        assert len(detected) == 1
        occasion = detected[0]
        assert occasion.situation_type == "personal_occasion"
        assert occasion.state == "pending"
        assert occasion.priority == "high"
        assert occasion.delivered_at is None
        assert "D-7" in occasion.why_now
        assert occasion.evidence.facts["days_until"] == "7"
        assert occasion.evidence.facts["occurrence"] == "2026-07-18"

        # When: the user starts a session and the agent checks in.
        received = harness.service.proactive_check()

        # Then: that agent receives the occasion and owns its delivery.
        assert tuple(item.id for item in received.situations) == (occasion.id,)
        assert received.situations[0].state == "delivered"
        assert received.situations[0].priority == "high"
        assert received.held_count == 0

        # When: the same session checks a second time.
        repeated = harness.service.proactive_check()

        # Then: a received occasion is never raised twice, and the empty
        # result is still not an all-clear while a source is unhealthy (§7).
        assert repeated.situations == ()
        assert repeated.held_count == 0
        assert repeated.all_clear is False

        # When: the user reports having handled it.
        acknowledged = harness.service.acknowledge_situation(occasion.id)

        # Then: the occasion leaves the delivery loop.
        assert acknowledged.state == "acknowledged"

        # Given: Google was connected that day and has not synced since.
        harness.store.record_sync_success("gmail")
        harness.store.record_sync_success("calendar")

        # When: the agent checks in on the D-7 morning of the next year.
        harness.clock.set(_NEXT_LEAD_MORNING)
        next_year = harness.service.proactive_check()
        states = tuple(
            item.state for item in harness.store.situations.list_situations()
        )

    # Then: the next occurrence is delivered as its own situation...
    assert len(next_year.situations) == 1
    renewed = next_year.situations[0]
    assert renewed.id != occasion.id
    assert renewed.state == "delivered"
    assert "D-7" in renewed.why_now
    assert renewed.evidence.facts["occurrence"] == "2027-07-18"
    assert states == ("acknowledged", "delivered")

    # ... while the year-old Gmail sync is reported instead of an all-clear (§7).
    assert next_year.freshness.gmail.status == "stale"
    assert "gmail: source is stale" in next_year.warnings
    assert next_year.all_clear is False
