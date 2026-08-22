from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from proactive_mcp.delivery.daemon import (
    DaemonDependencies,
    DaemonSchedule,
    WatcherDaemon,
)
from proactive_mcp.delivery.evaluation import SkippedSources
from proactive_mcp.paths import resolve_paths
from proactive_mcp.scheduler import EventScheduler
from proactive_mcp.store import Store
from tests.daemon_test_support import (
    FakeEvaluationRunner,
    RecordingHeartbeat,
    RecordingNotifier,
    RecordingScheduler,
    birthday_memory,
    local_only_pass,
    open_local_evaluation,
)
from tests.situation_test_support import FakeClock, utc_datetime

_PID = 4242
_POLL_INTERVAL = timedelta(minutes=5)
_START = utc_datetime(2026, 8, 21, 12)


def _daemon(
    heartbeat: RecordingHeartbeat,
    evaluation: FakeEvaluationRunner,
    notifier: RecordingNotifier,
) -> WatcherDaemon:
    return WatcherDaemon(
        DaemonDependencies(
            pid=_PID,
            clock=evaluation.clock,
            heartbeat=heartbeat,
            evaluation=evaluation,
            notifier=notifier,
        )
    )


def test_once_path_runs_exactly_one_pass_and_never_waits() -> None:
    # Given: a watcher whose collaborators record every call they receive.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(
        result=local_only_pass(),
        clock=clock,
        duration=timedelta(minutes=2),
    )
    heartbeat = RecordingHeartbeat()
    notifier = RecordingNotifier(situation_ids=(7,))

    # When: the library once-path runs.
    completed = _daemon(heartbeat, runner, notifier).run_once()

    # Then: exactly one pass ran and the process ended without a wait.
    assert len(runner.passes) == 1
    assert heartbeat.events == [f"start:{_PID}", "heartbeat", "stop"]
    assert len(completed.notifications) == 1
    assert notifier.dispatches == [_START + timedelta(minutes=2)]
    assert clock.now() == _START + timedelta(minutes=2)


def test_continuous_loop_waits_the_poll_interval_left_after_each_pass() -> None:
    # Given: five-minute polling and passes that each take two minutes.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(
        result=local_only_pass(),
        clock=clock,
        duration=timedelta(minutes=2),
    )
    scheduler = RecordingScheduler(stop_after=3)
    heartbeat = RecordingHeartbeat()

    # When: the loop runs until the scheduler stops it.
    run = _daemon(heartbeat, runner, RecordingNotifier()).run_forever(
        DaemonSchedule(scheduler=scheduler, poll_interval=_POLL_INTERVAL)
    )

    # Then: every wait is the cadence remainder, with one heartbeat per pass.
    assert run.pass_count == 3
    assert scheduler.waits == [timedelta(minutes=3)] * 3
    assert heartbeat.events == [
        f"start:{_PID}",
        "heartbeat",
        "heartbeat",
        "heartbeat",
        "stop",
    ]


def test_pass_longer_than_the_poll_interval_schedules_no_negative_wait() -> None:
    # Given: a pass that overruns the five-minute poll interval.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(
        result=local_only_pass(),
        clock=clock,
        duration=timedelta(minutes=9),
    )
    scheduler = RecordingScheduler(stop_after=1)

    # When: the loop schedules the wait after that overrunning pass.
    _ = _daemon(RecordingHeartbeat(), runner, RecordingNotifier()).run_forever(
        DaemonSchedule(scheduler=scheduler, poll_interval=_POLL_INTERVAL)
    )

    # Then: the next pass starts at once instead of waiting negative time.
    assert scheduler.waits == [timedelta()]


def test_loop_totals_the_fallback_notifications_of_every_pass() -> None:
    # Given: a notifier that reports two notified situations per pass.
    clock = FakeClock(_START)
    runner = FakeEvaluationRunner(result=local_only_pass(), clock=clock)
    notifier = RecordingNotifier(situation_ids=(11, 12))

    # When: two passes complete.
    run = _daemon(RecordingHeartbeat(), runner, notifier).run_forever(
        DaemonSchedule(
            scheduler=RecordingScheduler(stop_after=2),
            poll_interval=_POLL_INTERVAL,
        )
    )

    # Then: the run reports the notification total of both passes.
    assert notifier.dispatches == [_START, _START]
    assert run.notification_count == 4


def test_daemon_passes_never_claim_delivery_of_a_detected_situation(
    tmp_path: Path,
) -> None:
    # Given: a D-7 birthday and a watcher on the shared evaluation service.
    clock = FakeClock(utc_datetime(2026, 7, 11, 9))
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        _ = store.remember(birthday_memory())
        daemon = WatcherDaemon(
            DaemonDependencies(
                pid=_PID,
                clock=clock,
                heartbeat=store.daemon,
                evaluation=open_local_evaluation(store, clock),
                notifier=RecordingNotifier(),
            )
        )

        # When: the watcher completes two scheduled passes.
        run = daemon.run_forever(
            DaemonSchedule(
                scheduler=RecordingScheduler(stop_after=2),
                poll_interval=_POLL_INTERVAL,
            )
        )
        pending = store.situations.list_situations(state="pending")
        delivered = store.situations.list_situations(state="delivered")
        status = store.daemon.status(stale_after=_POLL_INTERVAL)

    # Then: the detected situation still waits for an agent to receive it.
    assert run.pass_count == 2
    assert tuple(item.situation_type for item in pending) == ("personal_occasion",)
    assert pending[0].delivered_at is None
    assert delivered == ()
    assert status.cycle_count == 2


def test_daemon_pass_reports_why_no_remote_source_was_read(tmp_path: Path) -> None:
    # Given: a watcher whose credentials do not permit a Google read.
    clock = FakeClock(utc_datetime(2026, 7, 11, 9))
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        daemon = WatcherDaemon(
            DaemonDependencies(
                pid=_PID,
                clock=clock,
                heartbeat=store.daemon,
                evaluation=open_local_evaluation(store, clock),
                notifier=RecordingNotifier(),
            )
        )

        # When: one pass runs with no source snapshot available.
        completed = daemon.run_once()

    # Then: the skip is named, so an empty result is never an all-clear.
    assert completed.evaluation.sources == SkippedSources("missing_credentials")
    assert completed.evaluation.result.created == 0
    assert "gmail: skipped this pass (no snapshot); situations kept" in (
        completed.evaluation.warnings
    )
    assert "gmail: source is not_configured" in completed.evaluation.warnings


def test_event_scheduler_stops_the_loop_once_a_stop_is_requested() -> None:
    # Given: a scheduler that has already been asked to stop.
    scheduler = EventScheduler()
    scheduler.stop()

    # When: the loop asks for its next wait.
    proceed = scheduler.wait(timedelta(hours=1))

    # Then: the wait returns at once and ends the loop.
    assert proceed is False


def test_event_scheduler_keeps_the_loop_running_without_a_stop_request() -> None:
    # Given: a scheduler nobody has stopped.
    scheduler = EventScheduler()

    # When: an elapsed cadence needs no further wait.
    proceed = scheduler.wait(timedelta())

    # Then: the loop continues with the next pass.
    assert proceed is True


def test_state_paths_are_derived_from_the_database_override(tmp_path: Path) -> None:
    # Given: an installation whose database lives outside the home directory.
    database = tmp_path / "state" / "proactive.db"

    # When: the process resolves its state layout.
    paths = resolve_paths({"PROACTIVE_DATABASE": str(database)})

    # Then: config and credentials share the database directory (§4.2).
    assert paths.database == database
    assert paths.config == tmp_path / "state" / "config.toml"
    assert paths.state_directory == tmp_path / "state"


def test_state_paths_default_to_the_documented_home_directory() -> None:
    # Given: an environment with no database override.
    # When: the process resolves its state layout.
    paths = resolve_paths({})

    # Then: the documented per-user location is used and expanded.
    assert paths.database == Path("~/.proactive-mcp/proactive.db").expanduser()
    assert paths.config == paths.database.parent / "config.toml"
