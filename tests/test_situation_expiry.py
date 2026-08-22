from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.store import (
    DeliveryClaim,
    Detection,
    InvalidSituationTransitionError,
    Situation,
    SituationEvidence,
    Store,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_NOW = utc_datetime(2026, 8, 21, 16)


def _detection(key: str, *, expires_at: datetime | None = None) -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority="critical",
        title="Fixture conflict",
        why_now="Fixture starts within two hours",
        evidence=SituationEvidence(facts={"source_id": key}),
        expires_at=expires_at,
    )


def _delivery(now: datetime) -> DeliveryClaim:
    return DeliveryClaim(
        delivered_at=now.isoformat(),
        cooldown_after=(now - timedelta(hours=24)).isoformat(),
        local_day_start=(now - timedelta(hours=16)).isoformat(),
        local_day_end=(now + timedelta(hours=8)).isoformat(),
        daily_budget=4,
        allow_noncritical=True,
    )


def _by_key(store: Store, key: str) -> Situation:
    found = tuple(
        item for item in store.situations.list_situations() if item.dedupe_key == key
    )
    assert len(found) == 1
    return found[0]


def test_expire_lapsed_expires_pending_rows_no_agent_ever_received(
    tmp_path: Path,
) -> None:
    # Given: pending rows on both sides of their relevance window.
    clock = FakeClock(_NOW)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (
                _detection("lapsed", expires_at=_NOW),
                _detection("still-relevant", expires_at=_NOW + timedelta(minutes=1)),
                _detection("open-ended"),
            )
        )

        # When: the engine expires lapsed situations.
        expired = store.situations.expire_lapsed()
        states = {
            item.dedupe_key: item.state for item in store.situations.list_situations()
        }

    # Then: only the lapsed pending row leaves the active set.
    assert expired == 1
    assert states == {
        "lapsed": "expired",
        "still-relevant": "pending",
        "open-ended": "pending",
    }


def test_expire_lapsed_still_expires_delivered_rows(tmp_path: Path) -> None:
    # Given: one delivered row whose window has passed.
    clock = FakeClock(_NOW)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("seen", expires_at=_NOW),))
        _ = store.situations.mark_delivered((_by_key(store, "seen").id,))

        # When: the engine expires lapsed situations.
        expired = store.situations.expire_lapsed()
        state = _by_key(store, "seen").state

    # Then: delivered rows keep their existing expiry behavior.
    assert expired == 1
    assert state == "expired"


def test_delivery_claim_rejects_a_row_past_its_relevance_window(
    tmp_path: Path,
) -> None:
    # Given: one lapsed and one still-relevant pending row.
    clock = FakeClock(_NOW)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (
                _detection("lapsed", expires_at=_NOW),
                _detection("still-relevant", expires_at=_NOW + timedelta(minutes=1)),
            )
        )

        # When: a delivery claim runs before the expiry sweep.
        claimed = store.situations.claim_for_delivery(_delivery(_NOW))
        lapsed = _by_key(store, "lapsed")

    # Then: the lapsed row is never handed to an agent and stays pending.
    assert tuple(item.dedupe_key for item in claimed) == ("still-relevant",)
    assert lapsed.state == "pending"


def test_mute_type_scope_mutes_the_instance_and_registers_the_type(
    tmp_path: Path,
) -> None:
    # Given: one delivered row and one pending sibling of the same type.
    clock = FakeClock(_NOW)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (_detection("named"), _detection("sibling"))
        )
        named = _by_key(store, "named")
        _ = store.situations.mark_delivered((named.id,))

        # When: the user mutes the named situation with type scope.
        muted = store.situations.mute_situation_type(named.id)
        claimed = store.situations.claim_for_delivery(_delivery(_NOW))
        muted_types = store.situations.muted_situation_types()

    # Then: the instance is muted and the whole type stops being delivered.
    assert muted.state == "muted"
    assert muted_types == ("calendar_conflict",)
    assert claimed == ()


def test_mute_type_scope_registers_nothing_when_the_instance_rejects_it(
    tmp_path: Path,
) -> None:
    # Given: a situation no agent has delivered yet.
    clock = FakeClock(_NOW)
    with Store(tmp_path / "db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("undelivered"),))
        pending = _by_key(store, "undelivered")

        # When/Then: the rejected transition leaves no type mute behind.
        with pytest.raises(InvalidSituationTransitionError):
            _ = store.situations.mute_situation_type(pending.id)
        assert store.situations.muted_situation_types() == ()
        assert _by_key(store, "undelivered").state == "pending"
