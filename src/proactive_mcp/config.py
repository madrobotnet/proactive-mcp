"""Typed attention and detector settings backed by config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from importlib import import_module
from typing import TYPE_CHECKING, Final, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from proactive_mcp.config_parse import (
    ConfigError,
    PriorityName,
    parse_bool,
    parse_count,
    parse_priorities,
    parse_span,
    parse_time,
    parse_timezone_name,
)
from proactive_mcp.config_raw import RawConfig

ConfigError.__module__ = __name__

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AttentionSettings",
    "ConfigError",
    "DaemonSettings",
    "DetectorSettings",
    "FallbackSettings",
    "ProactiveConfig",
    "SourceSettings",
    "load_config",
    "resolve_timezone",
]

_MAX_DAILY_BUDGET: Final = 1000
_MAX_LEAD_DAYS: Final = 365


class _LocalZoneProvider(Protocol):
    def __call__(self) -> tzinfo | None: ...


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

    priorities: tuple[PriorityName, ...] = ("critical",)
    wait: timedelta = timedelta(minutes=30)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Provider read windows; Gmail lookback default follows §6.1."""

    gmail_lookback: timedelta = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ProactiveConfig:
    """All user-tunable situation engine settings."""

    attention: AttentionSettings = field(default_factory=AttentionSettings)
    detectors: DetectorSettings = field(default_factory=DetectorSettings)
    daemon: DaemonSettings = field(default_factory=DaemonSettings)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)
    sources: SourceSettings = field(default_factory=SourceSettings)


def load_config(path: Path) -> ProactiveConfig:
    """Load settings from one config.toml path, defaulting absent values."""
    try:
        raw = RawConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return ProactiveConfig()
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        ValidationError,
    ) as error:
        raise ConfigError(field="config.toml", reason="cannot be parsed") from error
    high = parse_span(
        raw.detectors.calendar_high_hours,
        "calendar_high_hours",
        timedelta(hours=24),
    )
    critical = parse_span(
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
            quiet_hours_start=parse_time(
                raw.attention.quiet_hours_start, "quiet_hours_start", time(21, 0)
            ),
            quiet_hours_end=parse_time(
                raw.attention.quiet_hours_end, "quiet_hours_end", time(7, 0)
            ),
            daily_budget=parse_count(
                raw.attention.daily_budget, "daily_budget", (4, _MAX_DAILY_BUDGET)
            ),
            cooldown=parse_span(
                raw.attention.cooldown_hours, "cooldown_hours", timedelta(hours=24)
            ),
            timezone=parse_timezone_name(raw.attention.timezone),
        ),
        detectors=DetectorSettings(
            reply_threshold=parse_span(
                raw.detectors.reply_threshold_hours,
                "reply_threshold_hours",
                timedelta(hours=48),
            ),
            calendar_high_window=high,
            calendar_critical_window=critical,
            occasion_default_lead_days=parse_count(
                raw.detectors.occasion_default_lead_days,
                "occasion_default_lead_days",
                (7, _MAX_LEAD_DAYS),
            ),
        ),
        daemon=DaemonSettings(
            poll_interval=parse_span(
                raw.daemon.poll_interval_minutes,
                "poll_interval_minutes",
                timedelta(minutes=5),
            ),
        ),
        fallback=FallbackSettings(
            priorities=parse_priorities(raw.fallback.priorities),
            wait=parse_span(
                raw.fallback.wait_minutes, "wait_minutes", timedelta(minutes=30)
            ),
            enabled=parse_bool(
                raw.fallback.enabled,
                "fallback.enabled",
                default=True,
            ),
        ),
        sources=SourceSettings(
            gmail_lookback=parse_span(
                raw.sources.gmail_lookback_days,
                "gmail_lookback_days",
                timedelta(days=7),
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
