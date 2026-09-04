"""Convention-based optional service backend discovery."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from typing_extensions import override

if TYPE_CHECKING:
    from proactive_mcp.cli.service_models import ServiceAction, ServiceCommandResult


@runtime_checkable
class ServiceBackend(Protocol):
    """Module contract implemented by an optional platform backend."""

    def execute_service(self, action: ServiceAction) -> ServiceCommandResult:
        """Run one service lifecycle action without presentation I/O."""
        ...


@dataclass(frozen=True, slots=True)
class InvalidServiceBackendError(RuntimeError):
    """Report a platform module that does not implement the backend contract."""

    module_name: str

    @override
    def __str__(self) -> str:
        return f"service backend does not implement execute_service: {self.module_name}"


def platform_executor(platform: str) -> ServiceBackend | None:
    """Load `service_<platform>` when that typed module exists."""
    if not platform.isidentifier():
        return None
    module_name = f"proactive_mcp.cli.service_{platform}"
    if find_spec(module_name) is None:
        return None
    module = import_module(module_name)
    if not isinstance(module, ServiceBackend):
        raise InvalidServiceBackendError(module_name)
    return module
