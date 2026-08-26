"""Shared UTC serialization for situation persistence."""

from datetime import UTC, datetime


def utc_iso(value: datetime) -> str:
    """Serialize one timezone-aware datetime in UTC."""
    return value.astimezone(UTC).isoformat()
