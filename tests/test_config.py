from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.config import (
    ConfigError,
    DaemonSettings,
    DetectorSettings,
    FallbackSettings,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_detector_settings_default_occasion_lead_is_seven_days() -> None:
    # Given: no detector overrides.

    # When: detector settings use product defaults.
    settings = DetectorSettings()

    # Then: undated row overrides fall back to seven days.
    assert settings.occasion_default_lead_days == 7


def test_daemon_settings_default_poll_interval_is_five_minutes() -> None:
    # Given: no daemon overrides.

    # When: daemon settings use product defaults.
    settings = DaemonSettings()

    # Then: the watcher polls every five minutes.
    assert settings.poll_interval == timedelta(minutes=5)


def test_fallback_settings_default_to_critical_after_thirty_minutes() -> None:
    # Given: no fallback overrides.

    # When: fallback settings use product defaults.
    settings = FallbackSettings()

    # Then: only critical situations wait thirty minutes for an agent.
    assert settings.priorities == ("critical",)
    assert settings.wait == timedelta(minutes=30)


def test_load_config_reads_occasion_default_lead_days(tmp_path: Path) -> None:
    # Given: TOML overrides the personal-occasion fallback.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        "[detectors]\noccasion_default_lead_days = 11\n",
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    settings = load_config(config_path).detectors

    # Then: the typed detector settings expose the override.
    assert settings.occasion_default_lead_days == 11


def test_load_config_uses_m4_defaults_when_file_is_missing(tmp_path: Path) -> None:
    # Given: no config.toml exists on disk.
    missing = tmp_path / "config.toml"

    # When: configuration is loaded through the TOML boundary.
    settings = load_config(missing)

    # Then: daemon and fallback product defaults are used.
    assert settings.daemon.poll_interval == timedelta(minutes=5)
    assert settings.fallback.priorities == ("critical",)
    assert settings.fallback.wait == timedelta(minutes=30)


def test_load_config_reads_daemon_and_fallback_overrides(tmp_path: Path) -> None:
    # Given: TOML overrides daemon cadence and fallback policy away from defaults.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        """\
[daemon]
poll_interval_minutes = 7
[fallback]
priorities = ["high", "routine"]
wait_minutes = 12
""",
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    settings = load_config(config_path)

    # Then: typed daemon and fallback settings expose the overrides.
    assert settings.daemon.poll_interval == timedelta(minutes=7)
    assert settings.fallback.priorities == ("high", "routine")
    assert settings.fallback.wait == timedelta(minutes=12)


def test_load_config_keeps_existing_overrides_when_m4_tables_are_set(
    tmp_path: Path,
) -> None:
    # Given: attention, detector, daemon, and fallback tables are all present.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        """\
[attention]
daily_budget = 3
[detectors]
occasion_default_lead_days = 11
[daemon]
poll_interval_minutes = 8
[fallback]
priorities = ["high"]
wait_minutes = 45
""",
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    settings = load_config(config_path)

    # Then: pre-M4 overrides survive beside the new sections.
    assert settings.attention.daily_budget == 3
    assert settings.detectors.occasion_default_lead_days == 11
    assert settings.daemon.poll_interval == timedelta(minutes=8)
    assert settings.fallback.priorities == ("high",)
    assert settings.fallback.wait == timedelta(minutes=45)


def test_load_config_rejects_critical_window_larger_than_high_window(
    tmp_path: Path,
) -> None:
    # Given: TOML inverts the calendar priority windows.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        "[detectors]\ncalendar_critical_hours = 3\ncalendar_high_hours = 2\n",
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    with pytest.raises(ConfigError) as raised:
        _ = load_config(config_path)

    # Then: the invalid relationship is rejected as a typed config error.
    assert raised.value.field == "calendar_critical_hours"
    assert raised.value.reason == "must not exceed calendar_high_hours"


def test_load_config_rejects_empty_fallback_priorities(tmp_path: Path) -> None:
    # Given: fallback priorities are an empty list.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        "[fallback]\npriorities = []\n",
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    with pytest.raises(ConfigError) as raised:
        _ = load_config(config_path)

    # Then: empty priorities cannot cross the config boundary.
    assert raised.value.field == "priorities"
    assert raised.value.reason == "must not be empty"


def test_load_config_rejects_duplicate_fallback_priorities(tmp_path: Path) -> None:
    # Given: fallback priorities repeat a valid name.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        '[fallback]\npriorities = ["critical", "critical"]\n',
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    with pytest.raises(ConfigError) as raised:
        _ = load_config(config_path)

    # Then: duplicate priorities cannot cross the config boundary.
    assert raised.value.field == "priorities"
    assert raised.value.reason == "must not contain duplicates"


def test_load_config_rejects_unknown_fallback_priorities(tmp_path: Path) -> None:
    # Given: fallback priorities include a name outside the closed set.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(
        '[fallback]\npriorities = ["urgent"]\n',
        encoding="utf-8",
    )

    # When: configuration is loaded through the TOML boundary.
    with pytest.raises(ConfigError) as raised:
        _ = load_config(config_path)

    # Then: unknown priorities cannot cross the config boundary.
    assert raised.value.field == "priorities"
    assert raised.value.reason == "must be one of critical, high, routine"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ("[daemon]\npoll_interval_minutes = 0\n", "poll_interval_minutes"),
        ("[daemon]\npoll_interval_minutes = -1\n", "poll_interval_minutes"),
        ("[fallback]\nwait_minutes = 0\n", "wait_minutes"),
        ("[fallback]\nwait_minutes = -2\n", "wait_minutes"),
    ],
)
def test_load_config_rejects_non_positive_durations(
    tmp_path: Path,
    body: str,
    field: str,
) -> None:
    # Given: a daemon or fallback duration is zero or negative.
    config_path = tmp_path / "config.toml"
    _ = config_path.write_text(body, encoding="utf-8")

    # When: configuration is loaded through the TOML boundary.
    with pytest.raises(ConfigError) as raised:
        _ = load_config(config_path)

    # Then: non-positive durations cannot cross the config boundary.
    assert raised.value.field == field
    assert raised.value.reason == "must be a positive number of minutes"
