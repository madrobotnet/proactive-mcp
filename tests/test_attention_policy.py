from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from proactive_mcp import config as config_module
from proactive_mcp import situations
from proactive_mcp.config import AttentionSettings
from proactive_mcp.store import Detection, SituationEvidence, Store
from tests.situation_test_support import FakeClock, require_m3, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.store import SituationPriority


def _detection(
    key: str,
    priority: SituationPriority = "routine",
) -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority=priority,
        title=f"Fixture {key}",
        why_now="Fixture attention candidate",
        evidence=SituationEvidence(facts={"source_id": key}),
    )


def _policy(store: Store) -> situations.AttentionPolicy:
    require_m3("AttentionPolicy")
    return situations.AttentionPolicy(
        store.situations,
        ZoneInfo("America/New_York"),
        AttentionSettings(
            quiet_hours_start=time(21),
            quiet_hours_end=time(7),
            daily_budget=4,
            cooldown=timedelta(hours=24),
            timezone="America/New_York",
        ),
    )


def test_default_quiet_hours_timezone_preserves_dst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the host's local IANA timezone observes daylight saving time.
    monkeypatch.setattr(
        config_module,
        "_get_localzone",
        lambda: ZoneInfo("America/New_York"),
        raising=False,
    )

    # When: no explicit timezone is configured.
    timezone = config_module.resolve_timezone(
        None,
        now=datetime(2026, 8, 21, 12, tzinfo=UTC),
    )

    # Then: the default zone preserves seasonal offset rules.
    winter_offset = datetime(2026, 1, 15, 12, tzinfo=timezone).utcoffset()
    summer_offset = datetime(2026, 7, 15, 12, tzinfo=timezone).utcoffset()
    assert winter_offset != summer_offset


@pytest.mark.parametrize(
    ("local_now", "expected_ids"),
    [
        (datetime(2026, 1, 15, 6, 59, tzinfo=ZoneInfo("America/New_York")), ()),
        (datetime(2026, 1, 15, 7, 0, tzinfo=ZoneInfo("America/New_York")), (1,)),
        (datetime(2026, 1, 15, 20, 59, tzinfo=ZoneInfo("America/New_York")), (1,)),
        (datetime(2026, 1, 15, 21, 0, tzinfo=ZoneInfo("America/New_York")), ()),
    ],
)
def test_quiet_hours_obey_inclusive_start_exclusive_end_boundaries(
    tmp_path: Path,
    local_now: datetime,
    expected_ids: tuple[int, ...],
) -> None:
    # Given: one routine pending situation and the default overnight window.
    clock = FakeClock(local_now.astimezone(UTC))
    with Store(tmp_path / "attention.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("boundary"),))
        policy = _policy(store)

        # When: an exact local boundary is evaluated from its UTC instant.
        selected = policy.select_for_delivery(clock.now())

        # Then: 21:00 is quiet and 07:00 is available.
        assert tuple(item.id for item in selected) == expected_ids


def test_quiet_hours_remain_local_across_dst_gap_and_fold(tmp_path: Path) -> None:
    # Given: one pending item and real instants around both New York transitions.
    timezone = ZoneInfo("America/New_York")
    local_instants = (
        datetime(2026, 3, 8, 1, 59, tzinfo=timezone),
        datetime(2026, 3, 8, 3, 0, tzinfo=timezone),
        datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=timezone),
        datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=timezone),
    )
    clock = FakeClock(local_instants[0].astimezone(UTC))
    with Store(tmp_path / "attention.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("dst"),))
        policy = _policy(store)

        # When: each instant is evaluated through the configured IANA zone.
        observed = tuple(
            policy.select_for_delivery(value.astimezone(UTC))
            for value in local_instants
        )

        # Then: skipped and repeated wall-clock periods stay quiet.
        assert observed == ((), (), (), ())


def test_daily_budget_prioritizes_and_resets_on_local_date(tmp_path: Path) -> None:
    # Given: five pending non-critical items and no delivery history.
    now = utc_datetime(2026, 8, 21, 16)
    clock = FakeClock(now)
    with Store(tmp_path / "attention.db", clock=clock) as store:
        detections = tuple(
            _detection(f"key-{index}", priority)
            for index, priority in enumerate(
                ("routine", "high", "routine", "high", "routine"),
                start=1,
            )
        )
        _ = store.situations.upsert_detections(detections)
        policy = _policy(store)

        # When: four are selected and delivered, then the local date advances.
        first = policy.select_for_delivery(now)
        _ = store.situations.mark_delivered(tuple(item.id for item in first))
        second = policy.select_for_delivery(now + timedelta(days=1))

        # Then: high priority leads, four use day one, and one rolls over.
        assert tuple(item.dedupe_key for item in first) == (
            "key-2",
            "key-4",
            "key-1",
            "key-3",
        )
        assert tuple(item.dedupe_key for item in second) == ("key-5",)


def test_critical_bypasses_quiet_hours_and_exhausted_budget(tmp_path: Path) -> None:
    # Given: quiet local time after four routine deliveries today.
    local_now = datetime(2026, 8, 21, 22, tzinfo=ZoneInfo("America/New_York"))
    now = local_now.astimezone(UTC)
    clock = FakeClock(now)
    with Store(tmp_path / "attention.db", clock=clock) as store:
        used = tuple(_detection(f"used-{index}") for index in range(4))
        _ = store.situations.upsert_detections(used)
        used_ids = tuple(item.id for item in store.situations.list_situations())
        _ = store.situations.mark_delivered(used_ids)
        _ = store.situations.upsert_detections(
            (_detection("held"), _detection("critical", "critical"))
        )

        # When: policy applies quiet hours and the exhausted daily budget.
        selected = _policy(store).select_for_delivery(now)

        # Then: only critical bypasses both suppression rules.
        assert tuple(item.dedupe_key for item in selected) == ("critical",)


def test_cooldown_blocks_until_twenty_four_hour_boundary(tmp_path: Path) -> None:
    # Given: a resolved situation that was delivered and then detected again.
    delivered_at = utc_datetime(2026, 8, 20, 12)
    clock = FakeClock(delivered_at)
    with Store(tmp_path / "attention.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("same-key", "high"),))
        first = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((first.id,))
        _ = store.situations.resolve_absent("calendar_conflict", ())
        _ = store.situations.upsert_detections((_detection("same-key", "high"),))
        policy = _policy(store)

        # When: policy evaluates immediately before and exactly at expiry.
        before = policy.select_for_delivery(
            delivered_at + timedelta(hours=24, microseconds=-1)
        )
        at_boundary = policy.select_for_delivery(delivered_at + timedelta(hours=24))

        # Then: the exact 24-hour instant is eligible.
        assert before == ()
        assert tuple(item.dedupe_key for item in at_boundary) == ("same-key",)
