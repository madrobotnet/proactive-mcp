"""Typed response contract for service lifecycle commands."""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

__all__ = [
    "HeartbeatState",
    "LingerState",
    "ServiceAction",
    "ServiceCode",
    "ServiceResponse",
    "ServiceState",
]

ServiceAction: TypeAlias = Literal["install", "status", "remove"]
ServiceState: TypeAlias = Literal[
    "installed",
    "active",
    "inactive",
    "removed",
    "absent",
    "unsupported",
    "failed",
    "unmanaged",
]
ServiceCode: TypeAlias = Literal[
    "unsupported_platform",
    "binary_not_found",
    "unmanaged_unit",
    "command_failed",
    "heartbeat_unavailable",
    "io_failed",
]
LingerState: TypeAlias = Literal["enabled", "disabled", "unknown", "not_applicable"]
HeartbeatState: TypeAlias = Literal["running", "stopped", "stale", "never_started"]


class ServiceResponse(BaseModel):
    """Machine-readable result of one service lifecycle operation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    action: ServiceAction
    state: ServiceState
    unit: str
    managed: bool
    enabled: bool
    active: bool
    main_pid: int | None
    heartbeat: HeartbeatState | None
    linger: LingerState
    guidance: Literal["none", "enable_linger"]
    code: ServiceCode | None
