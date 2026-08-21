"""Typed attention and detector settings backed by config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from importlib import import_module
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, TypeAlias, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AttentionSettings",
    "ConfigError",
    "DetectorSettings",
    "ProactiveConfig",
    "load_config",
    "resolve_timezone",
]

_HOURS_PER_DAY: Final = 24
_MAX_DAILY_BUDGET: Final = 1000
_TomlValue: TypeAlias = bool | int | float | str | date | time | datetime


class _LocalZoneProvider(Protocol):
    def __call__(self) -> object:
        """Return a local timezone candidate."""


class _RawAttention(BaseModel):
    """Untrusted attention values parsed from TOML."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="ignore",
        strict=True,
    )

    quiet_hours_start: _TomlValue | None = None
    quiet_hours_end: _TomlValue | None = None
    daily_budget: _TomlValue | None = None
    cooldown_hours: _TomlValue | None = None
    timezone: _TomlValue | None = None


class _RawDetectors(BaseModel):
    """Untrusted detector values parsed from TOML."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="ignore",
        strict=True,
    )

    reply_threshold_hours: _TomlValue | None = None
    calendar_high_hours: _TomlValue | None = None
    calendar_critical_hours: _TomlValue | None = None


class _RawConfig(BaseModel):
    """Typed trust boundary for config.toml."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="ignore",
        strict=True,
    )

    attention: _RawAttention = Field(default_factory=_RawAttention)
    detectors: _RawDetectors = Field(default_factory=_RawDetectors)


@dataclass(frozen=True, slots=True)
class ConfigError(Exception):
    """Raised when config.toml holds a value the model cannot represent."""

    field: str
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"invalid config {self.field}: {self.reason}")


@dataclass(frozen=True, slots=True)
class AttentionSettings:
    """Attention policy values; defaults follow the product plan §7."""

    quiet_hours_start: time = time(21, 0)
    quiet_hours_end: time = time(7, 0)
    daily_budget: int = 4
    cooldown: timedelta = timedelta(hours=24)
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class DetectorSettings:
    """Deterministic detector thresholds; defaults follow §6."""

    reply_threshold: timedelta = timedelta(hours=48)
    calendar_high_window: timedelta = timedelta(hours=24)
    calendar_critical_window: timedelta = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class ProactiveConfig:
    """All user-tunable situation engine settings."""

    attention: AttentionSettings = field(default_factory=AttentionSettings)
    detectors: DetectorSettings = field(default_factory=DetectorSettings)


def load_config(path: Path) -> ProactiveConfig:
    """Load settings from one config.toml path, defaulting absent values."""
    try:
        raw = _RawConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return ProactiveConfig()
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        ValidationError,
    ) as error:
        raise ConfigError(field="config.toml", reason="cannot be parsed") from error
    attention = raw.attention
    detectors = raw.detectors
    return ProactiveConfig(
        attention=AttentionSettings(
            quiet_hours_start=_parse_time(
                attention.quiet_hours_start,
                "quiet_hours_start",
                time(21, 0),
            ),
            quiet_hours_end=_parse_time(
                attention.quiet_hours_end,
                "quiet_hours_end",
                time(7, 0),
            ),
            daily_budget=_parse_budget(attention.daily_budget),
            cooldown=_parse_hours(
                attention.cooldown_hours,
                "cooldown_hours",
                timedelta(hours=24),
            ),
            timezone=_parse_timezone_name(attention.timezone),
        ),
        detectors=DetectorSettings(
            reply_threshold=_parse_hours(
                detectors.reply_threshold_hours,
                "reply_threshold_hours",
                timedelta(hours=48),
            ),
            calendar_high_window=_parse_hours(
                detectors.calendar_high_hours,
                "calendar_high_hours",
                timedelta(hours=24),
            ),
            calendar_critical_window=_parse_hours(
                detectors.calendar_critical_hours,
                "calendar_critical_hours",
                timedelta(hours=2),
            ),
        ),
    )


def resolve_timezone(name: str | None, *, now: datetime) -> tzinfo:
    """Return the configured IANA timezone, or the system-local IANA zone.

    The local-zone fallback preserves seasonal rules across DST transitions.
    ``now`` remains injected so no wall-clock read occurs in attention logic.
    """
    if name is not None:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConfigError(
                field="timezone",
                reason="is not a known IANA timezone",
            ) from error
    local = now.astimezone(_get_localzone()).tzinfo
    if local is None:
        raise ConfigError(field="timezone", reason="system timezone is unavailable")
    return local


def _get_localzone() -> tzinfo:
    """Load and type-check the local-zone provider at the package boundary."""
    candidate = getattr(import_module("tzlocal"), "get_localzone", None)
    if not callable(candidate):
        raise ConfigError(field="timezone", reason="local timezone is unavailable")
    provider = cast("_LocalZoneProvider", candidate)
    zone = provider()
    if not isinstance(zone, tzinfo):
        raise ConfigError(field="timezone", reason="local timezone is unavailable")
    return zone


def _parse_time(value: _TomlValue | None, key: str, default: time) -> time:
    if value is None:
        return default
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise ConfigError(field=key, reason="must be an HH:MM string")
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(field=key, reason="must be an HH:MM string") from error


def _parse_budget(value: _TomlValue | None) -> int:
    if value is None:
        return 4
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_DAILY_BUDGET
    ):
        raise ConfigError(
            field="daily_budget",
            reason=f"must be an integer between 0 and {_MAX_DAILY_BUDGET}",
        )
    return value


def _parse_hours(
    value: _TomlValue | None,
    key: str,
    default: timedelta,
) -> timedelta:
    if value is None:
        return default
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= _HOURS_PER_DAY * 365
    ):
        raise ConfigError(field=key, reason="must be a positive number of hours")
    return timedelta(hours=value)


def _parse_timezone_name(value: _TomlValue | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(field="timezone", reason="must be an IANA timezone name")
    return value
