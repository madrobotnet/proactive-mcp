"""Current-user Windows Task Scheduler backend for the watcher daemon."""

from __future__ import annotations

import unicodedata
from base64 import b64encode
from pathlib import PurePath, PureWindowsPath
from typing import TYPE_CHECKING, Final
from xml.etree import ElementTree as ET

from proactive_mcp.cli.service_task_scheduler_contract import (
    MANAGED_TASK_MARKER,
    TASK_NAME,
    TaskSchedulerManager,
)
from proactive_mcp.cli.service_task_scheduler_lifecycle import (
    execute_task_scheduler_lifecycle,
)
from proactive_mcp.cli.service_task_scheduler_manager import (
    WindowsTaskSchedulerManager,
)
from proactive_mcp.cli.service_task_scheduler_ready import (
    READY_FILE_ENV,
    task_scheduler_ready_file,
)
from proactive_mcp.delivery.notify import trusted_notifier_path

if TYPE_CHECKING:
    from proactive_mcp.cli.service_models import ServiceAction, ServiceCommandResult

__all__ = [
    "MANAGED_TASK_MARKER",
    "TASK_NAME",
    "TaskSchedulerManager",
    "WindowsTaskSchedulerManager",
    "execute_task_scheduler",
    "is_managed_task",
    "render_task_definition",
]

_TASK_NAMESPACE: Final = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", _TASK_NAMESPACE)


def render_task_definition(
    executable: PurePath,
    database: PurePath,
    powershell: PurePath,
) -> str:
    """Render one safe, current-user Task Scheduler XML definition."""
    _validate_absolute_path(executable, "executable")
    _validate_absolute_path(database, "database")
    _validate_absolute_path(powershell, "PowerShell")
    trusted_powershell = PureWindowsPath(trusted_notifier_path("win32"))
    if PureWindowsPath(str(powershell)) != trusted_powershell:
        message = "PowerShell must be the trusted absolute System32 executable"
        raise ValueError(message)

    executable_token = b64encode(str(executable).encode()).decode("ascii")
    database_token = b64encode(str(database).encode()).decode("ascii")
    ready_file = task_scheduler_ready_file(database)
    ready_token = b64encode(str(ready_file).encode()).decode("ascii")
    launcher = (
        "$executable = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{executable_token}'))\n"
        "$database = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{database_token}'))\n"
        "$readyFile = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{ready_token}'))\n"
        "$env:PROACTIVE_DATABASE = $database\n"
        f"$env:{READY_FILE_ENV} = $readyFile\n"
        "& $executable 'daemon'\n"
        "exit $LASTEXITCODE\n"
    )
    encoded_launcher = b64encode(launcher.encode("utf-16-le")).decode("ascii")
    task = ET.Element(_tag("Task"), {"version": "1.2"})
    registration = ET.SubElement(task, _tag("RegistrationInfo"))
    ET.SubElement(registration, _tag("URI")).text = f"\\{TASK_NAME}"
    ET.SubElement(registration, _tag("Description")).text = "proactive-mcp watcher"
    ET.SubElement(registration, _tag("Documentation")).text = MANAGED_TASK_MARKER

    triggers = ET.SubElement(task, _tag("Triggers"))
    trigger = ET.SubElement(triggers, _tag("LogonTrigger"))
    ET.SubElement(trigger, _tag("Enabled")).text = "true"
    principals = ET.SubElement(task, _tag("Principals"))
    principal = ET.SubElement(principals, _tag("Principal"), {"id": "Author"})
    ET.SubElement(principal, _tag("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, _tag("RunLevel")).text = "LeastPrivilege"

    settings = ET.SubElement(task, _tag("Settings"))
    ET.SubElement(settings, _tag("MultipleInstancesPolicy")).text = "IgnoreNew"
    ET.SubElement(settings, _tag("AllowStartOnDemand")).text = "true"
    ET.SubElement(settings, _tag("Enabled")).text = "true"
    ET.SubElement(settings, _tag("ExecutionTimeLimit")).text = "PT0S"
    restart = ET.SubElement(settings, _tag("RestartOnFailure"))
    ET.SubElement(restart, _tag("Interval")).text = "PT1M"
    ET.SubElement(restart, _tag("Count")).text = "3"
    ET.SubElement(task, _tag("Data")).text = executable_token

    actions = ET.SubElement(task, _tag("Actions"), {"Context": "Author"})
    action = ET.SubElement(actions, _tag("Exec"))
    ET.SubElement(action, _tag("Command")).text = str(trusted_powershell)
    ET.SubElement(action, _tag("Arguments")).text = (
        "-NoLogo -NoProfile -NonInteractive -EncodedCommand " + encoded_launcher
    )
    return ET.tostring(task, encoding="unicode")


def is_managed_task(definition: str) -> bool:
    """Return whether XML contains an exact managed Documentation element."""
    try:
        root = ET.fromstring(definition)  # noqa: S314
    except ET.ParseError:
        return False
    return any(
        child.tag.rpartition("}")[2] == "Documentation"
        and (child.text or "").strip() == MANAGED_TASK_MARKER
        for registration in root.iter()
        if registration.tag.rpartition("}")[2] == "RegistrationInfo"
        for child in registration
    )


def execute_task_scheduler(
    action: ServiceAction,
    manager: TaskSchedulerManager | None = None,
) -> ServiceCommandResult:
    """Run one Windows Task Scheduler lifecycle operation without output."""
    selected = WindowsTaskSchedulerManager() if manager is None else manager
    return execute_task_scheduler_lifecycle(action, selected, _render_default)


def _render_default(executable: PurePath, database: PurePath) -> str:
    powershell = PureWindowsPath(trusted_notifier_path("win32"))
    return render_task_definition(executable, database, powershell)


def _validate_absolute_path(path: PurePath, label: str) -> None:
    value = str(path)
    if not path.is_absolute():
        message = f"{label} path must be absolute"
        raise ValueError(message)
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        message = f"{label} path contains an unsafe control character"
        raise ValueError(message)


def _tag(local_name: str) -> str:
    return f"{{{_TASK_NAMESPACE}}}{local_name}"
