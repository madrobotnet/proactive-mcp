from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.config import FallbackSettings
from proactive_mcp.delivery.fallback import (
    FallbackDispatcher,
    FallbackFailed,
    FallbackSent,
)
from proactive_mcp.delivery.notify import (
    DEFAULT_NOTIFICATION_TIMEOUT,
    NotificationHost,
)
from tests.fallback_test_support import (
    NOTIFY,
    NOW,
    SECOND,
    SEOUL,
    WAIT,
    FailingRunner,
    RecordingRunner,
    aged,
    by_key,
    claimed,
    detection,
    dispatch,
)

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.delivery.notify import NotificationErrorCode
    from proactive_mcp.store import FallbackFailureCode


def test_situation_one_second_before_the_wait_is_not_toasted(tmp_path: Path) -> None:
    # Given: a routine bootstrap candidate detected inside the wait window.
    with aged(
        tmp_path,
        detection("too-recent", priority="routine"),
        age=WAIT - SECOND,
    ) as store:
        runner = RecordingRunner()

        # When: the dispatcher runs at T+29:59.
        dispatched = dispatch(store, runner)

        # Then: nothing is sent and no claim blocks a later toast.
        assert dispatched == ()
        assert runner.calls == []
        assert claimed(store) == ()


def test_situation_exactly_at_the_wait_boundary_is_toasted(tmp_path: Path) -> None:
    # Given: a critical situation detected exactly the configured wait ago.
    with aged(tmp_path, detection("at-boundary")) as store:
        runner = RecordingRunner()
        situation = by_key(store, "at-boundary")

        # When: the dispatcher runs at T+30:00.
        dispatched = dispatch(store, runner)
        history = store.fallbacks.history(situation.id)

        # Then: the boundary is inclusive and one bounded send is recorded.
        assert dispatched == (FallbackSent(situation.id),)
        assert runner.calls == [(*NOTIFY, "Calendar conflict", "calendar_conflict")]
        assert runner.timeouts == [DEFAULT_NOTIFICATION_TIMEOUT]
        assert history is not None
        assert (history.outcome, history.failure_code) == ("sent", None)


def test_wait_boundary_is_evaluated_in_utc_for_a_non_utc_now(tmp_path: Path) -> None:
    # Given: a critical situation detected exactly the wait ago.
    with aged(tmp_path, detection("offset-now")) as store:
        runner = RecordingRunner()
        dispatcher = FallbackDispatcher(
            store.fallbacks,
            NotificationHost("linux", runner),
        )

        # When: the same instant arrives expressed in a +09:00 zone.
        dispatched = dispatcher.dispatch(NOW.astimezone(SEOUL))

        # Then: the zone offset does not shift the boundary.
        assert dispatched == (FallbackSent(by_key(store, "offset-now").id),)


def test_configured_wait_overrides_the_default_thirty_minutes(tmp_path: Path) -> None:
    # Given: a routine bootstrap candidate detected six minutes ago.
    with aged(
        tmp_path,
        detection("short-wait", priority="routine"),
        age=timedelta(minutes=6),
    ) as store:
        runner = RecordingRunner()

        # When: config shortens the wait to five minutes.
        dispatched = dispatch(
            store,
            runner,
            FallbackSettings(wait=timedelta(minutes=5)),
        )

        # Then: the configured wait, not the 30-minute default, decides.
        assert dispatched == (FallbackSent(by_key(store, "short-wait").id),)


def test_successful_toast_is_recorded_once_and_never_repeated(tmp_path: Path) -> None:
    # Given: one aged critical situation.
    with aged(tmp_path, detection("one-shot")) as store:
        runner = RecordingRunner()
        situation = by_key(store, "one-shot")

        # When: the dispatcher runs twice over the same boundary.
        first = dispatch(store, runner)
        second = dispatch(store, runner)

        # Then: the send happened exactly once.
        assert first == (FallbackSent(situation.id),)
        assert second == ()
        assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("error_code", "failure_code"),
    [
        ("timeout", "timeout"),
        ("unavailable", "tool_missing"),
        ("failed", "nonzero_exit"),
        ("unsupported_platform", "unsupported_platform"),
    ],
)
def test_failed_toast_records_its_code_and_is_never_retried(
    tmp_path: Path,
    error_code: NotificationErrorCode,
    failure_code: FallbackFailureCode,
) -> None:
    # Given: an aged critical situation and a notifier that fails.
    with aged(tmp_path, detection("fails")) as store:
        runner = FailingRunner(error_code)
        situation = by_key(store, "fails")

        # When: the dispatcher runs twice after the send fails.
        first = dispatch(store, runner)
        second = dispatch(store, runner)
        history = store.fallbacks.history(situation.id)

        # Then: the failure is visible once, enumerated, and never retried.
        assert first == (FallbackFailed(situation.id, failure_code),)
        assert second == ()
        assert len(runner.recorder.calls) == 1
        assert history is not None
        assert (history.outcome, history.failure_code) == ("failed", failure_code)
