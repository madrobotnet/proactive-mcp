from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from textwrap import dedent
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from proactive_mcp import situations
from proactive_mcp.config import AttentionSettings, load_config
from proactive_mcp.store import (
    DEFAULT_STALE_AFTER,
    Detection,
    SituationEvidence,
    Store,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.store import SituationPriority


def require_m4(*names: str) -> None:
    missing = tuple(name for name in names if not hasattr(situations, name))
    assert not missing, f"missing M4 situation API: {', '.join(missing)}"


def _detection(
    key: str,
    priority: SituationPriority = "routine",
) -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key=key,
        priority=priority,
        title=f"Fixture {key}",
        why_now="Fixture delivery candidate",
        evidence=SituationEvidence(facts={"source_id": key}),
    )


def _policy(store: Store) -> situations.AttentionPolicy:
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


def _skipped(source: str) -> str:
    return f"{source}: skipped this pass (no snapshot); situations kept"


def test_claim_persists_utc_timestamps_when_clock_is_not_utc(tmp_path: Path) -> None:
    require_m4("AttentionPolicy")
    # Given: a pending situation and a New York wall-clock instant.
    timezone = ZoneInfo("America/New_York")
    local_now = datetime(2026, 8, 21, 8, tzinfo=timezone)
    clock = FakeClock(local_now)
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("offset"),))
        policy = _policy(store)

        # When: the policy claims delivery from the non-UTC clock.
        claimed = policy.claim_for_delivery(clock.now())

        # Then: every persisted claim timestamp is lexicographic UTC.
        expected = local_now.astimezone(UTC).isoformat()
        assert tuple(item.delivered_at for item in claimed) == (expected,)
        assert expected == "2026-08-21T12:00:00+00:00"
        persisted = store.situations.get_situation(claimed[0].id)
        assert persisted is not None
        assert persisted.delivered_at == expected
        assert persisted.updated_at == expected


def test_budget_usage_reports_exact_noncritical_used_and_remaining(
    tmp_path: Path,
) -> None:
    require_m4("BudgetUsage")
    # Given: one yesterday routine plus two today routines and one critical.
    clock = FakeClock(utc_datetime(2026, 8, 20, 16))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        _ = store.situations.upsert_detections((_detection("yesterday"),))
        yesterday = store.situations.list_situations()[0]
        _ = store.situations.mark_delivered((yesterday.id,))
        clock.set(utc_datetime(2026, 8, 21, 16))
        _ = store.situations.upsert_detections(
            (
                _detection("today-a"),
                _detection("today-b"),
                _detection("critical", "critical"),
            )
        )
        pending = tuple(
            item.id
            for item in store.situations.list_situations()
            if item.state == "pending"
        )
        _ = store.situations.mark_delivered(pending)
        policy = _policy(store)

        # When: status/check asks for today's typed budget usage.
        usage = policy.budget_usage(clock.now())

        # Then: only today's non-critical deliveries consume the daily 4.
        assert usage == situations.BudgetUsage(used=2, remaining=2, daily_budget=4)


def test_quiet_state_is_active_only_inside_the_local_window(tmp_path: Path) -> None:
    require_m4("QuietState")
    # Given: default overnight quiet hours in America/New_York.
    timezone = ZoneInfo("America/New_York")
    clock = FakeClock(datetime(2026, 8, 21, 22, tzinfo=timezone).astimezone(UTC))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        policy = _policy(store)

        # When: status/check evaluates the quiet state at 22:00 and 12:00 local.
        late = policy.quiet_state(datetime(2026, 8, 21, 22, tzinfo=timezone))
        midday = policy.quiet_state(datetime(2026, 8, 21, 12, tzinfo=timezone))

        # Then: only the in-window instant is active.
        assert late == situations.QuietState(active=True)
        assert midday == situations.QuietState(active=False)


def test_runtime_retains_the_loaded_proactive_config(tmp_path: Path) -> None:
    require_m4("SituationRuntime")
    # Given: a config file whose values all differ from the product defaults.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        dedent(
            """\
            [attention]
            timezone = "UTC"
            quiet_hours_start = "08:00"
            daily_budget = 3
            [detectors]
            occasion_default_lead_days = 11
            """
        ),
        encoding="utf-8",
    )
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        # When: the production factory loads that file once.
        runtime = situations.SituationRuntime.from_config(store, clock, config_path)

        # Then: callers can read the same loaded config the policy used.
        assert runtime.config == load_config(config_path)
        assert runtime.config.attention.daily_budget == 3
        assert runtime.config.attention.quiet_hours_start == time(8, 0)
        assert runtime.config.detectors.occasion_default_lead_days == 11


def test_skipped_fresh_source_omits_generic_skip_warning(tmp_path: Path) -> None:
    require_m4("SituationEngine")
    # Given: both Google sources succeeded well inside the freshness window.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        store.record_sync_success("calendar")
        engine = situations.SituationEngine(store, clock, UTC)

        # When: a local-only pass skips both already-fresh snapshots.
        result = engine.evaluate(situations.EngineInputs())

        # Then: a fresh skip is silent and may honestly report all-clear.
        assert result.gmail_freshness.status == "ok"
        assert result.calendar_freshness.status == "ok"
        assert _skipped("gmail") not in result.warnings
        assert _skipped("calendar") not in result.warnings
        assert result.warnings == ()


def test_skipped_stale_source_never_reports_all_clear(tmp_path: Path) -> None:
    require_m4("SituationEngine")
    # Given: Gmail succeeded once and then aged past the stale threshold.
    clock = FakeClock(utc_datetime(2026, 8, 20, 12))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_success("gmail")
        clock.advance(DEFAULT_STALE_AFTER)
        engine = situations.SituationEngine(store, clock, UTC)

        # When: the next pass skips Gmail instead of supplying a snapshot.
        result = engine.evaluate(situations.EngineInputs())

        # Then: stale absence cannot be presented as an all-clear.
        assert result.gmail_freshness.status == "stale"
        assert "gmail: source is stale" in result.warnings
        assert _skipped("gmail") in result.warnings
        assert result.warnings != ()


def test_skipped_error_source_keeps_error_and_skip_warnings(tmp_path: Path) -> None:
    require_m4("SituationEngine")
    # Given: Gmail is configured but its last sync failed.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        store.record_sync_failure("gmail", error_code="network")
        engine = situations.SituationEngine(store, clock, UTC)

        # When: evaluation skips the failed source.
        result = engine.evaluate(situations.EngineInputs())

        # Then: the error warning survives alongside the skip warning.
        assert result.gmail_freshness.status == "error"
        assert "gmail: source is error" in result.warnings
        assert _skipped("gmail") in result.warnings


def test_skipped_unconfigured_source_keeps_not_configured_warning(
    tmp_path: Path,
) -> None:
    require_m4("SituationEngine")
    # Given: a store that has never completed Google setup.
    clock = FakeClock(utc_datetime(2026, 8, 21, 12))
    with Store(tmp_path / "delivery.db", clock=clock) as store:
        engine = situations.SituationEngine(store, clock, UTC)

        # When: evaluation skips both unconfigured sources.
        result = engine.evaluate(situations.EngineInputs())

        # Then: not-configured sources still warn instead of going silent.
        assert result.gmail_freshness.status == "not_configured"
        assert result.calendar_freshness.status == "not_configured"
        assert "gmail: source is not_configured" in result.warnings
        assert "calendar: source is not_configured" in result.warnings
        assert _skipped("gmail") in result.warnings
        assert _skipped("calendar") in result.warnings
