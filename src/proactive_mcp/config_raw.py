"""Raw pydantic models for untrusted config.toml tables."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import ClassVar, Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

TomlValue: TypeAlias = bool | int | float | str | date | time | datetime
RAW_MODEL: Final[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class RawAttention(BaseModel):
    """Untrusted attention table values from config.toml."""

    model_config: ClassVar[ConfigDict] = RAW_MODEL
    quiet_hours_start: TomlValue | None = None
    quiet_hours_end: TomlValue | None = None
    daily_budget: TomlValue | None = None
    cooldown_hours: TomlValue | None = None
    timezone: TomlValue | None = None


class RawDetectors(BaseModel):
    """Untrusted detector table values from config.toml."""

    model_config: ClassVar[ConfigDict] = RAW_MODEL
    reply_threshold_hours: TomlValue | None = None
    calendar_high_hours: TomlValue | None = None
    calendar_critical_hours: TomlValue | None = None
    occasion_default_lead_days: TomlValue | None = None


class RawSlice(BaseModel):
    """Untrusted daemon or fallback table values from config.toml."""

    model_config: ClassVar[ConfigDict] = RAW_MODEL
    poll_interval_minutes: TomlValue | None = None
    priorities: list[TomlValue] | None = None
    wait_minutes: TomlValue | None = None


class RawSources(BaseModel):
    """Untrusted sources table values from config.toml."""

    model_config: ClassVar[ConfigDict] = RAW_MODEL
    gmail_lookback_days: TomlValue | None = None


class RawConfig(BaseModel):
    """Untrusted top-level config.toml document."""

    model_config: ClassVar[ConfigDict] = RAW_MODEL
    attention: RawAttention = Field(default_factory=RawAttention)
    detectors: RawDetectors = Field(default_factory=RawDetectors)
    daemon: RawSlice = Field(default_factory=RawSlice)
    fallback: RawSlice = Field(default_factory=RawSlice)
    sources: RawSources = Field(default_factory=RawSources)
