from __future__ import annotations

import os

import pytest

from proactive_mcp.cli.service_launchd import (
    LaunchctlResult,
    LaunchdInspectionError,
    LaunchdState,
    LaunchdUserManager,
)
from tests.service_launchd_manager_support import FakeLaunchctlRunner


def _manager(
    *,
    disabled: LaunchctlResult,
    service: LaunchctlResult | None = None,
) -> LaunchdUserManager:
    responses = {("/bin/launchctl", "print-disabled", "gui/501"): disabled}
    if service is not None:
        responses[("/bin/launchctl", "print", "gui/501/com.proactive.mcp")] = service
    return LaunchdUserManager(
        "com.proactive.mcp",
        uid=501,
        runner=FakeLaunchctlRunner(responses),
    )


def test_manager_is_enabled_parses_explicit_and_default_states() -> None:
    disabled = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output='disabled services = {\n"com.proactive.mcp" => true\n}',
            exit_code=0,
        )
    )
    enabled = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output='disabled services = {\n"com.proactive.mcp" => false\n}',
            exit_code=0,
        )
    )
    default_enabled = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output='disabled services = {\n"other.service" => true\n}',
            exit_code=0,
        )
    )

    assert disabled.is_enabled() is False
    assert enabled.is_enabled() is True
    assert default_enabled.is_enabled() is True


def test_manager_is_enabled_fails_closed_on_error_or_malformed_output() -> None:
    failed = _manager(disabled=LaunchctlResult(succeeded=False, output="", exit_code=1))
    malformed = _manager(
        disabled=LaunchctlResult(succeeded=True, output="garbage", exit_code=0)
    )

    with pytest.raises(LaunchdInspectionError, match="print-disabled command failed"):
        _ = failed.is_enabled()
    with pytest.raises(LaunchdInspectionError, match="print-disabled malformed"):
        _ = malformed.is_enabled()


def test_manager_rejects_partially_malformed_disabled_output() -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output=(
                'disabled services = {\n"com.proactive.mcp" => maybe\n'
                '"other.service" => true\n}'
            ),
            exit_code=0,
        )
    )

    with pytest.raises(LaunchdInspectionError, match="print-disabled malformed"):
        _ = manager.is_enabled()


@pytest.mark.parametrize(
    "service",
    [
        LaunchctlResult(succeeded=False, output="", exit_code=-1),
        LaunchctlResult(
            succeeded=False,
            output="Not privileged to inspect service",
            exit_code=1,
        ),
    ],
)
def test_manager_state_rejects_unknown_inspection_failures(
    service: LaunchctlResult,
) -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output="disabled services = {}",
            exit_code=0,
        ),
        service=service,
    )

    with pytest.raises(LaunchdInspectionError, match="print command failed"):
        _ = manager.state()


def test_manager_state_reports_running_pid() -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output="disabled services = {}",
            exit_code=0,
        ),
        service=LaunchctlResult(
            succeeded=True,
            output="state = running\npid = 4321\n",
            exit_code=0,
        ),
    )

    assert manager.state() == LaunchdState(
        loaded=True,
        enabled=True,
        active=True,
        pid=4321,
    )


def test_manager_state_reports_waiting_as_inactive() -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output="disabled services = {}",
            exit_code=0,
        ),
        service=LaunchctlResult(
            succeeded=True,
            output="state = waiting\n",
            exit_code=0,
        ),
    )

    assert manager.state() == LaunchdState(
        loaded=True,
        enabled=True,
        active=False,
        pid=None,
    )


def test_manager_state_reports_unloaded() -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output="disabled services = {}",
            exit_code=0,
        ),
        service=LaunchctlResult(
            succeeded=False,
            output="Could not find service",
            exit_code=113,
        ),
    )

    assert manager.state() == LaunchdState(
        loaded=False,
        enabled=True,
        active=False,
        pid=None,
    )


def test_manager_state_malformed_pid_fails_closed() -> None:
    manager = _manager(
        disabled=LaunchctlResult(
            succeeded=True,
            output="disabled services = {}",
            exit_code=0,
        ),
        service=LaunchctlResult(
            succeeded=True,
            output="state = running\npid = nope\n",
            exit_code=0,
        ),
    )

    assert manager.state() == LaunchdState(
        loaded=True,
        enabled=True,
        active=False,
        pid=None,
    )


def test_manager_default_uid_uses_getuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 502)
    manager = LaunchdUserManager("com.proactive.mcp", runner=FakeLaunchctlRunner())

    assert manager.target == "gui/502/com.proactive.mcp"
    assert manager.domain == "gui/502"
