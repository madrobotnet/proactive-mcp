"""Typed attention and detector settings backed by config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from importlib import import_module
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, TypeAlias, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AttentionSettings",
    "ConfigError",
    "DaemonSettings",
    "DetectorSettings",
    "FallbackSettings",
    "ProactiveConfig",
    "load_config",
    "resolve_timezone",
]

_MAX_DAILY_BUDGET: Final = 1000
_MAX_LEAD_DAYS: Final = 365
_TomlValue: TypeAlias = bool | int | float | str | date | time | datetime
_PriorityName: TypeAlias = Literal["critical", "high", "routine"]
_PRIORITIES: Final[dict[str, _PriorityName]] = {
    "critical": "critical",
    "high": "high",
    "routine": "routine",
}
_RAW_MODEL: Final[ConfigDict] = ConfigDict(frozen=True, extra="ignore", strict=True)


class _LocalZoneProvider(Protocol):
    def __call__(self) -> tzinfo | None: ...


class _RawAttention(BaseModel):
    model_config: ClassVar[ConfigDict] = _RAW_MODEL
    quiet_hours_start: _TomlValue | None = None
    quiet_hours_end: _TomlValue | None = None
    daily_budget: _TomlValue | None = None
    cooldown_hours: _TomlValue | None = None
    timezone: _TomlValue | None = None


class _RawDetectors(BaseModel):
    model_config: ClassVar[ConfigDict] = _RAW_MODEL
    reply_threshold_hours: _TomlValue | None = None
    calendar_high_hours: _TomlValue | None = None
    calendar_critical_hours: _TomlValue | None = None
    occasion_default_lead_days: _TomlValue | None = None


class _RawSlice(BaseModel):
    model_config: ClassVar[ConfigDict] = _RAW_MODEL
    poll_interval_minutes: _TomlValue | None = None
    priorities: list[_TomlValue] | None = None
    wait_minutes: _TomlValue | None = None


class _RawConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _RAW_MODEL
    attention: _RawAttention = Field(default_factory=_RawAttention)
    detectors: _RawDetectors = Field(default_factory=_RawDetectors)
    daemon: _RawSlice = Field(default_factory=_RawSlice)
    fallback: _RawSlice = Field(default_factory=_RawSlice)


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
    occasion_default_lead_days: int = 7


@dataclass(frozen=True, slots=True)
class DaemonSettings:
    """Watcher poll cadence; default follows §4.1."""

    poll_interval: timedelta = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class FallbackSettings:
    """OS-notification fallback policy; defaults follow §7."""

    priorities: tuple[_PriorityName, ...] = ("critical",)
    wait: timedelta = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class ProactiveConfig:
    """All user-tunable situation engine settings."""

    attention: AttentionSettings = field(default_factory=AttentionSettings)
    detectors: DetectorSettings = field(default_factory=DetectorSettings)
    daemon: DaemonSettings = field(default_factory=DaemonSettings)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)


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
    high = _parse_span(
        raw.detectors.calendar_high_hours,
        "calendar_high_hours",
        timedelta(hours=24),
    )
    critical = _parse_span(
        raw.detectors.calendar_critical_hours,
        "calendar_critical_hours",
        timedelta(hours=2),
    )
    if critical > high:
        raise ConfigError(
            field="calendar_critical_hours",
            reason="must not exceed calendar_high_hours",
        )
    return ProactiveConfig(
        attention=AttentionSettings(
            quiet_hours_start=_parse_time(
                raw.attention.quiet_hours_start, "quiet_hours_start", time(21, 0)
            ),
            quiet_hours_end=_parse_time(
                raw.attention.quiet_hours_end, "quiet_hours_end", time(7, 0)
            ),
            daily_budget=_parse_count(
                raw.attention.daily_budget, "daily_budget", (4, _MAX_DAILY_BUDGET)
            ),
            cooldown=_parse_span(
                raw.attention.cooldown_hours, "cooldown_hours", timedelta(hours=24)
            ),
            timezone=_parse_timezone_name(raw.attention.timezone),
        ),
        detectors=DetectorSettings(
            reply_threshold=_parse_span(
                raw.detectors.reply_threshold_hours,
                "reply_threshold_hours",
                timedelta(hours=48),
            ),
            calendar_high_window=high,
            calendar_critical_window=critical,
            occasion_default_lead_days=_parse_count(
                raw.detectors.occasion_default_lead_days,
                "occasion_default_lead_days",
                (7, _MAX_LEAD_DAYS),
            ),
        ),
        daemon=DaemonSettings(
            poll_interval=_parse_span(
                raw.daemon.poll_interval_minutes,
                "poll_interval_minutes",
                timedelta(minutes=5),
            ),
        ),
        fallback=FallbackSettings(
            priorities=_parse_priorities(raw.fallback.priorities),
            wait=_parse_span(
                raw.fallback.wait_minutes, "wait_minutes", timedelta(minutes=30)
            ),
        ),
    )


def resolve_timezone(name: str | None, *, now: datetime) -> tzinfo:
    """Return the configured IANA timezone, or the system-local IANA zone."""
    if name is not None:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConfigError(
                field="timezone", reason="is not a known IANA timezone"
            ) from error
    local = now.astimezone(_get_localzone()).tzinfo
    if local is None:
        raise ConfigError(field="timezone", reason="system timezone is unavailable")
    return local


def _get_localzone() -> tzinfo:
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


def _parse_count(value: _TomlValue | None, key: str, spec: tuple[int, int]) -> int:
    default, maximum = spec
    if value is None:
        return default
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= maximum
    ):
        raise ConfigError(
            field=key, reason=f"must be an integer between 0 and {maximum}"
        )
    return value


def _parse_span(value: _TomlValue | None, key: str, default: timedelta) -> timedelta:
    if value is None:
        return default
    unit = "minutes" if key.endswith("_minutes") else "hours"
    maximum = 60 * 24 * 365 if unit == "minutes" else 24 * 365
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= maximum
    ):
        raise ConfigError(field=key, reason=f"must be a positive number of {unit}")
    return timedelta(minutes=value) if unit == "minutes" else timedelta(hours=value)


def _parse_priorities(values: list[_TomlValue] | None) -> tuple[_PriorityName, ...]:
    if values is None:
        return ("critical",)
    if not values:
        raise ConfigError(field="priorities", reason="must not be empty")
    names: list[_PriorityName] = []
    seen: set[_PriorityName] = set()
    for item in values:
        name = _PRIORITIES.get(item) if isinstance(item, str) else None
        if name is None:
            raise ConfigError(
                field="priorities", reason="must be one of critical, high, routine"
            )
        if name in seen:
            raise ConfigError(field="priorities", reason="must not contain duplicates")
        seen.add(name)
        names.append(name)
    return tuple(names)


def _parse_timezone_name(value: _TomlValue | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(field="timezone", reason="must be an IANA timezone name")
    return value
