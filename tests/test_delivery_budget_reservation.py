from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from proactive_mcp.store import (
    DeliveryClaim,
    Detection,
    SituationEvidence,
    Store,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from proactive_mcp.store import Situation, SituationPriority, SituationType

    Deliverer = Callable[[Store, DeliveryClaim], tuple[Situation, ...]]

_NOON: Final = utc_datetime(2026, 8, 21, 12)
_DAY_START: Final = utc_datetime(2026, 8, 21)
_DAY_END: Final = utc_datetime(2026, 8, 22)
_COOLDOWN: Final = timedelta(hours=24)
_LEASE: Final = timedelta(minutes=2)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _detection(
    key: str,
    situation_type: SituationType = "reply_deadline",
    priority: SituationPriority = "high",
    expires_at: datetime | None = None,
) -> Detection:
    return Detection(
        situation_type=situation_type,
        dedupe_key=key,
        priority=priority,
        title=f"Fixture {key}",
        why_now="Fixture delivery candidate",
        evidence=SituationEvidence(facts={"source_id": key}),
        expires_at=expires_at,
    )


def _delivery_claim(
    now: datetime = _NOON,
    *,
    daily_budget: int = 2,
    day_start: datetime = _DAY_START,
    day_end: datetime = _DAY_END,
) -> DeliveryClaim:
    return DeliveryClaim(
        delivered_at=_iso(now),
        cooldown_after=_iso(now - _COOLDOWN),
        local_day_start=_iso(day_start),
        local_day_end=_iso(day_end),
        daily_budget=daily_budget,
        allow_noncritical=True,
    )


def _reserve(store: Store, claim: DeliveryClaim) -> tuple[Situation, ...]:
    """Lease deliverable rows the way an MCP host takes them."""
    reservation = store.situations.reserve_for_delivery(
        claim,
        claim_token=f"token-{claim.delivered_at}",
        expires_at=datetime.fromisoformat(claim.delivered_at) + _LEASE,
    )
    return reservation.situations


def _claim(store: Store, claim: DeliveryClaim) -> tuple[Situation, ...]:
    """Claim deliverable rows the way the unattended path takes them."""
    return store.situations.claim_for_delivery(claim)


_ENTRY_POINTS = pytest.mark.parametrize(
    "deliver",
    [_reserve, _claim],
    ids=["reserve_for_delivery", "claim_for_delivery"],
)


def _keys(situations: tuple[Situation, ...]) -> tuple[str, ...]:
    return tuple(item.dedupe_key for item in situations)


def _mute_type(store: Store, situation_type: SituationType) -> None:
    """Mute a whole type through the delivered-row path the tools use."""
    key = f"mute-anchor-{situation_type}"
    _ = store.situations.upsert_detections(
        (_detection(key, situation_type, "critical"),)
    )
    anchor = next(
        item
        for item in store.situations.list_situations(state="pending")
        if item.dedupe_key == key
    )
    _ = store.situations.mark_delivered((anchor.id,))
    _ = store.situations.mute_situation_type(anchor.id)


@_ENTRY_POINTS
def test_a_muted_non_reply_cannot_hold_budget_from_two_replies(
    tmp_path: Path,
    deliver: Deliverer,
) -> None:
    # Given: a muted calendar type and two replies under a two-per-day budget.
    clock = FakeClock(_NOON)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _mute_type(store, "calendar_conflict")
        _ = store.situations.upsert_detections(
            (
                _detection("muted-calendar", "calendar_conflict", "routine"),
                _detection("reply-1"),
                _detection("reply-2"),
            )
        )

        # When: the host takes everything today's budget allows.
        delivered = deliver(store, _delivery_claim())

    # Then: a row the mute silences reserves nothing from the replies (§7).
    assert _keys(delivered) == ("reply-1", "reply-2")


@_ENTRY_POINTS
def test_an_expired_non_reply_cannot_hold_budget_from_two_replies(
    tmp_path: Path,
    deliver: Deliverer,
) -> None:
    # Given: a lapsed calendar row no delivery path can still hand over.
    clock = FakeClock(_NOON)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (
                _detection(
                    "lapsed-calendar",
                    "calendar_conflict",
                    "routine",
                    expires_at=_NOON - timedelta(minutes=1),
                ),
                _detection("reply-1"),
                _detection("reply-2"),
            )
        )

        # When: the host takes everything today's budget allows.
        delivered = deliver(store, _delivery_claim())

    # Then: the lapsed row keeps no capacity away from the replies (§7).
    assert _keys(delivered) == ("reply-1", "reply-2")


@_ENTRY_POINTS
def test_a_cooling_down_non_reply_cannot_hold_budget_from_two_replies(
    tmp_path: Path,
    deliver: Deliverer,
) -> None:
    # Given: a calendar row delivered late yesterday and detected again today.
    yesterday_evening = _NOON - timedelta(hours=18)
    clock = FakeClock(yesterday_evening)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        cooling = _detection("cooling-calendar", "calendar_conflict", "routine")
        _ = store.situations.upsert_detections((cooling,))
        delivered_yesterday = store.situations.list_situations(state="pending")[0]
        _ = store.situations.mark_delivered((delivered_yesterday.id,))
        clock.set(_NOON)
        _ = store.situations.resolve_absent("calendar_conflict", ())
        _ = store.situations.upsert_detections(
            (cooling, _detection("reply-1"), _detection("reply-2"))
        )

        # When: the host takes everything today's budget allows.
        delivered = deliver(store, _delivery_claim())

    # Then: yesterday's delivery spends yesterday's budget, not today's (§7).
    assert _keys(delivered) == ("reply-1", "reply-2")


def test_a_lease_held_across_midnight_cannot_hold_the_new_days_budget(
    tmp_path: Path,
) -> None:
    # Given: a calendar row leased at 23:59 whose receipt is still outstanding.
    before_midnight = utc_datetime(2026, 8, 21, 23, 59)
    after_midnight = _DAY_END + timedelta(seconds=30)
    clock = FakeClock(before_midnight)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (
                _detection("leased-calendar", "calendar_conflict", "routine"),
                _detection("reply-1"),
                _detection("reply-2"),
            )
        )
        leased = _reserve(store, _delivery_claim(before_midnight, daily_budget=1))

        # When: the next local day opens while that lease is unexpired.
        clock.set(after_midnight)
        delivered = _reserve(
            store,
            _delivery_claim(
                after_midnight,
                day_start=_DAY_END,
                day_end=utc_datetime(2026, 8, 23),
            ),
        )

    # Then: a row this pass cannot lease twice reserves nothing today (§5.1).
    assert _keys(leased) == ("leased-calendar",)
    assert _keys(delivered) == ("reply-1", "reply-2")


@_ENTRY_POINTS
def test_a_deliverable_non_reply_keeps_capacity_during_a_reply_flood(
    tmp_path: Path,
    deliver: Deliverer,
) -> None:
    # Given: one deliverable calendar row buried under a flood of replies.
    clock = FakeClock(_NOON)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _ = store.situations.upsert_detections(
            (
                _detection("calendar", "calendar_conflict", "routine"),
                *(_detection(f"reply-{index}") for index in range(5)),
            )
        )

        # When: the host takes everything today's budget allows.
        delivered = deliver(store, _delivery_claim())

    # Then: replies still cannot crowd out the one non-reply slot (§7).
    assert _keys(delivered) == ("calendar", "reply-0")
