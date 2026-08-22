from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from proactive_mcp.config import ConfigError, DetectorSettings, load_config

if TYPE_CHECKING:
    from pathlib import Path


def test_detector_settings_default_occasion_lead_is_seven_days() -> None:
    # Given: no detector overrides.

    # When: detector settings use product defaults.
    settings = DetectorSettings()

    # Then: undated row overrides fall back to seven days.
    assert settings.occasion_default_lead_days == 7


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
