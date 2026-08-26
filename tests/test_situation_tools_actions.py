from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

import pytest

from proactive_mcp.server.situation_requests import SituationRequestError
from proactive_mcp.store import InvalidSituationTransitionError
from tests.situation_test_support import utc_datetime
from tests.situation_tool_support import deliver_one, open_harness, pending_detection

if TYPE_CHECKING:
    from pathlib import Path

_NOON = utc_datetime(2026, 8, 21, 12)
_PRIVATE_MARKER: Final = "PRIVATE-SNOOZE-MARKER"


def test_acknowledge_requires_delivery_first(tmp_path: Path) -> None:
    # Given: one detected situation no agent has received yet.
    with open_harness(tmp_path, _NOON) as harness:
        _ = harness.store.situations.upsert_detections((pending_detection("ack"),))
        pending = harness.store.situations.list_situations(state="pending")[0]

        # When: acknowledgement is attempted before and after delivery.
        with pytest.raises(InvalidSituationTransitionError):
            _ = harness.service.acknowledge_situation(pending.id)
        response = harness.service.proactive_check()
        assert response.receipt_token is not None
        _ = harness.service.confirm_delivery(response.receipt_token)
        delivered = response.situations[0]
        acknowledged = harness.service.acknowledge_situation(delivered.id)

    # Then: only the delivered row may be acknowledged (§5.1).
    assert acknowledged.id == pending.id
    assert acknowledged.state == "acknowledged"


def test_snooze_requires_a_timezone_aware_future_instant(tmp_path: Path) -> None:
    # Given: one delivered situation awaiting a user decision.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "snooze")

        # When: unparseable, naive, and past wake times are offered first.
        with pytest.raises(SituationRequestError) as unparseable:
            _ = harness.service.snooze_situation(delivered.id, _PRIVATE_MARKER)
        with pytest.raises(SituationRequestError):
            _ = harness.service.snooze_situation(delivered.id, "2026-08-21T14:00:00")
        with pytest.raises(SituationRequestError):
            _ = harness.service.snooze_situation(
                delivered.id, "2026-08-21T11:00:00+00:00"
            )
        snoozed = harness.service.snooze_situation(
            delivered.id, "2026-08-21T15:30:00+03:00"
        )

    # Then: only an aware future instant is accepted, normalized to UTC.
    assert _PRIVATE_MARKER not in str(unparseable.value)
    assert snoozed.state == "snoozed"
    assert snoozed.snoozed_until == "2026-08-21T12:30:00+00:00"


def test_snoozed_situation_returns_only_after_its_wake_time(tmp_path: Path) -> None:
    # Given: a delivered situation snoozed two hours out.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "later")
        snoozed = harness.service.snooze_situation(
            delivered.id, "2026-08-21T14:00:00+00:00"
        )

        # When: the agent checks before and after the wake time.
        before = harness.service.proactive_check()
        harness.clock.set(_NOON + timedelta(hours=3))
        after = harness.service.proactive_check()

    # Then: the wake time alone decides re-delivery.
    assert snoozed.state == "snoozed"
    assert before.situations == ()
    assert tuple(item.id for item in after.situations) == (delivered.id,)


def test_mute_instance_keeps_the_type_deliverable(tmp_path: Path) -> None:
    # Given: one delivered situation the user mutes by instance.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "muted-instance")

        # When: the instance is muted and a sibling of its type is detected.
        muted = harness.service.mute_situation(delivered.id, "instance")
        harness.clock.set(_NOON + timedelta(hours=1))
        _ = harness.store.situations.upsert_detections((pending_detection("sibling"),))
        response = harness.service.proactive_check()

    # Then: only that instance is silenced.
    assert muted.scope == "instance"
    assert muted.situation.state == "muted"
    assert muted.muted_types == ()
    assert len(response.situations) == 1
    assert response.situations[0].id != delivered.id


def test_mute_type_atomically_mutes_the_instance_and_its_type(tmp_path: Path) -> None:
    # Given: one delivered situation the user mutes by type.
    with open_harness(tmp_path, _NOON) as harness:
        delivered = deliver_one(harness, "muted-type")

        # When: the type is muted and a sibling of its type is detected.
        muted = harness.service.mute_situation(delivered.id, "type")
        harness.clock.set(_NOON + timedelta(hours=1))
        _ = harness.store.situations.upsert_detections((pending_detection("sibling"),))
        response = harness.service.proactive_check()

    # Then: instance and type are muted together and nothing else delivers.
    assert muted.scope == "type"
    assert muted.situation.state == "muted"
    assert muted.muted_types == ("calendar_conflict",)
    assert response.situations == ()
    assert response.held_count == 1
