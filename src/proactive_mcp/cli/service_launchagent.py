"""Render and safely manage the proactive-mcp macOS LaunchAgent plist."""

from __future__ import annotations

import os
import plistlib
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from pydantic import TypeAdapter
from typing_extensions import TypeAliasType, override

if TYPE_CHECKING:
    from pathlib import Path, PurePath

__all__ = [
    "LAUNCHAGENT_FILENAME",
    "LAUNCHAGENT_LABEL",
    "PlistValue",
    "UnmanagedArtifactError",
    "UnsafeServiceArtifactError",
    "UnsafeServiceValueError",
    "delete_launch_agent_artifact",
    "is_managed_launch_agent",
    "parse_launch_agent_plist",
    "read_launch_agent_artifact",
    "render_launch_agent",
    "write_launch_agent_artifact",
]

LAUNCHAGENT_LABEL: Final = "io.github.madrobotnet.proactive-mcp"
LAUNCHAGENT_FILENAME: Final = f"{LAUNCHAGENT_LABEL}.plist"
_MARKER_KEY: Final = "PROACTIVE_MCP_MANAGED"
_MARKER_VALUE: Final = "1"
_PRIVATE_MODE: Final = 0o600
_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf"})

PlistValue = TypeAliasType(
    "PlistValue",
    str | bool | int | list["PlistValue"] | dict[str, "PlistValue"],
)


_PLIST_ADAPTER: Final[TypeAdapter[PlistValue]] = TypeAdapter(PlistValue)


@dataclass(frozen=True, slots=True)
class UnsafeServiceValueError(ValueError):
    """Report a path value that cannot be placed in a service definition."""

    path: PurePath
    reason: Literal["control", "relative"]

    @override
    def __str__(self) -> str:
        match self.reason:
            case "control":
                return f"path contains control or format characters: {self.path}"
            case "relative":
                return f"path must be absolute: {self.path}"


@dataclass(frozen=True, slots=True)
class UnsafeServiceArtifactError(ValueError):
    """Report an unsafe filesystem identity at the managed plist path."""

    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.path}"


@dataclass(frozen=True, slots=True)
class UnmanagedArtifactError(ValueError):
    """Report an ownership-marker mismatch at the managed plist path."""

    path: Path

    @override
    def __str__(self) -> str:
        return f"LaunchAgent plist is not managed by proactive-mcp: {self.path}"


def render_launch_agent(executable: PurePath, database: PurePath) -> bytes:
    """Render a deterministic LaunchAgent plist with a literal argument vector."""
    executable_value = _validated_path(executable)
    database_value = _validated_path(database)
    payload: dict[str, PlistValue] = {
        "Label": LAUNCHAGENT_LABEL,
        "ProgramArguments": [executable_value, "daemon"],
        "EnvironmentVariables": {
            "PROACTIVE_DATABASE": database_value,
            _MARKER_KEY: _MARKER_VALUE,
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "Umask": 0o077,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def is_managed_launch_agent(content: bytes) -> bool:
    """Return whether plist content has the exact managed label and marker."""
    try:
        loaded = parse_launch_agent_plist(content)
    except plistlib.InvalidFileException:
        return False
    if not isinstance(loaded, dict):
        return False
    label = loaded.get("Label")
    environment = loaded.get("EnvironmentVariables")
    return (
        label == LAUNCHAGENT_LABEL
        and isinstance(environment, dict)
        and environment.get(_MARKER_KEY) == _MARKER_VALUE
    )


def parse_launch_agent_plist(content: bytes) -> PlistValue:
    """Parse plist bytes through a recursive typed boundary."""
    return _PLIST_ADAPTER.validate_python(plistlib.loads(content))


def read_launch_agent_artifact(path: Path) -> bytes | None:
    """Read a regular, current-owner, single-link plist without following links."""
    try:
        expected = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    _assert_safe_identity(expected, path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        observed = os.fstat(stream.fileno())
        _assert_safe_identity(observed, path)
        content = stream.read()
        current = path.stat(follow_symlinks=False)
    if (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino):
        raise UnsafeServiceArtifactError(path, "artifact identity changed while read")
    return content


def write_launch_agent_artifact(
    path: Path,
    content: bytes,
    *,
    mode: int = _PRIVATE_MODE,
) -> None:
    """Atomically write managed content without modifying an unmanaged target."""
    if not is_managed_launch_agent(content):
        raise UnmanagedArtifactError(path)
    previous = read_launch_agent_artifact(path)
    if previous is not None and not is_managed_launch_agent(previous):
        raise UnmanagedArtifactError(path)

    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), mode)
            _write_all(stream.fileno(), content)
            os.fsync(stream.fileno())
            _assert_safe_identity(os.fstat(stream.fileno()), temporary)
        _revalidate_managed_target(path, previous)
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def delete_launch_agent_artifact(path: Path) -> None:
    """Delete only an artifact whose exact managed identity was verified."""
    content = read_launch_agent_artifact(path)
    if content is None:
        return
    if not is_managed_launch_agent(content):
        raise UnmanagedArtifactError(path)
    _revalidate_managed_target(path, content)
    path.unlink()


def _validated_path(path: PurePath) -> str:
    if not path.is_absolute():
        raise UnsafeServiceValueError(path, "relative")
    value = str(path)
    if any(
        unicodedata.category(character) in _FORBIDDEN_CATEGORIES for character in value
    ):
        raise UnsafeServiceValueError(path, "control")
    return value


def _assert_safe_identity(observed: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(observed.st_mode):
        raise UnsafeServiceArtifactError(path, "artifact must be a regular file")
    if os.name != "nt" and observed.st_uid != os.getuid():
        raise UnsafeServiceArtifactError(path, "artifact owner does not match")
    if observed.st_nlink != 1:
        raise UnsafeServiceArtifactError(path, "artifact must have one hard link")


def _revalidate_managed_target(path: Path, previous: bytes | None) -> None:
    current = read_launch_agent_artifact(path)
    if current is not None and not is_managed_launch_agent(current):
        raise UnmanagedArtifactError(path)
    if current != previous:
        raise UnsafeServiceArtifactError(path, "artifact changed before mutation")


def _write_all(descriptor: int, content: bytes) -> None:
    written = 0
    while written < len(content):
        count = os.write(descriptor, content[written:])
        if count == 0:
            raise OSError
        written += count
