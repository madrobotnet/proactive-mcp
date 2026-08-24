"""Parse raw config.toml values into typed settings fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from proactive_mcp.config_raw import TomlValue

PriorityName: TypeAlias = Literal["critical", "high", "routine"]
PRIORITIES: Final[dict[str, PriorityName]] = {
    "critical": "critical",
    "high": "high",
    "routine": "routine",
}


@dataclass(frozen=True, slots=True)
class ConfigError(Exception):
    """Raised when config.toml holds a value the model cannot represent."""

    field: str
    reason: str

    def __post_init__(self) -> None:
        """Initialize the base exception with a boundary-safe message."""
        Exception.__init__(self, f"invalid config {self.field}: {self.reason}")


def parse_time(value: TomlValue | None, key: str, default: time) -> time:
    """Parse a TOML time or HH:MM string."""
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


def parse_count(value: TomlValue | None, key: str, spec: tuple[int, int]) -> int:
    """Parse a bounded non-negative integer TOML count."""
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


def parse_span(value: TomlValue | None, key: str, default: timedelta) -> timedelta:
    """Parse a positive numeric TOML duration into a timedelta."""
    if value is None:
        return default
    unit = (
        "minutes"
        if key.endswith("_minutes")
        else "days"
        if key.endswith("_days")
        else "hours"
    )
    maximum = (
        60 * 24 * 365 if unit == "minutes" else 365 if unit == "days" else 24 * 365
    )
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= maximum
    ):
        raise ConfigError(field=key, reason=f"must be a positive number of {unit}")
    if unit == "minutes":
        return timedelta(minutes=value)
    if unit == "days":
        return timedelta(days=value)
    return timedelta(hours=value)


def parse_priorities(values: list[TomlValue] | None) -> tuple[PriorityName, ...]:
    """Parse a unique closed set of fallback priority names."""
    if values is None:
        return ("critical",)
    if not values:
        raise ConfigError(field="priorities", reason="must not be empty")
    names: list[PriorityName] = []
    seen: set[PriorityName] = set()
    for item in values:
        name = PRIORITIES.get(item) if isinstance(item, str) else None
        if name is None:
            raise ConfigError(
                field="priorities", reason="must be one of critical, high, routine"
            )
        if name in seen:
            raise ConfigError(field="priorities", reason="must not contain duplicates")
        seen.add(name)
        names.append(name)
    return tuple(names)


def parse_timezone_name(value: TomlValue | None) -> str | None:
    """Parse an optional IANA timezone name."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(field="timezone", reason="must be an IANA timezone name")
    return value
