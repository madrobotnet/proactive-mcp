from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from proactive_mcp.cli.service_launchagent import (
    LAUNCHAGENT_FILENAME,
    LAUNCHAGENT_LABEL,
    delete_launch_agent_artifact,
    is_managed_launch_agent,
    read_launch_agent_artifact,
)
from proactive_mcp.cli.service_models import ServiceResponse
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterator

_SMOKE_ENABLED: Final = os.environ.get("PROACTIVE_REAL_LAUNCHD_SMOKE") == "1"
_LAUNCHCTL: Final = "/bin/launchctl"
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not _SMOKE_ENABLED,
    reason="requires the opt-in macOS launchd smoke job",
)


@dataclass(frozen=True, slots=True)
class _SmokeHarness:
    entrypoint: Path
    home: Path
    database: Path
    plist: Path
    target: str
    environment: dict[str, str]

    def service(self, action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.entrypoint, "service", action],
            capture_output=True,
            text=True,
            env=self.environment,
            check=False,
            timeout=30,
        )

    def inspect(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_LAUNCHCTL, "print", self.target],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def inspect_disabled(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_LAUNCHCTL, "print-disabled", f"gui/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )


def _cleanup_smoke(harness: _SmokeHarness) -> list[str]:
    failures: list[str] = []
    content = read_launch_agent_artifact(harness.plist)
    if content is not None:
        if not is_managed_launch_agent(content):
            failures.append("refused to delete unmanaged smoke plist")
        else:
            removed = harness.service("remove")
            if removed.returncode != 0:
                bootout = subprocess.run(
                    [_LAUNCHCTL, "bootout", harness.target],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if bootout.returncode != 0 and harness.inspect().returncode == 0:
                    failures.append("fallback bootout failed")
                delete_launch_agent_artifact(harness.plist)
                failures.append("public service remove failed")
    if harness.inspect().returncode == 0:
        failures.append("launchd label remained loaded")
    if harness.plist.exists():
        failures.append("managed smoke plist remained")
    if not failures:
        if harness.home.exists():
            shutil.rmtree(harness.home, ignore_errors=False)
        if harness.database.parent.exists():
            shutil.rmtree(harness.database.parent, ignore_errors=False)
    return failures


@pytest.fixture
def launchd_smoke(tmp_path: Path) -> Iterator[_SmokeHarness]:
    home = tmp_path / "home"
    database = tmp_path / "state" / "proactive.db"
    plist = home / "Library" / "LaunchAgents" / LAUNCHAGENT_FILENAME
    target = f"gui/{os.getuid()}/{LAUNCHAGENT_LABEL}"
    harness = _SmokeHarness(
        entrypoint=Path(sys.executable).with_name("proactive-mcp"),
        home=home,
        database=database,
        plist=plist,
        target=target,
        environment=os.environ
        | {
            "HOME": str(home),
            "PROACTIVE_DATABASE": str(database),
        },
    )
    assert harness.entrypoint.is_absolute()
    assert harness.entrypoint.is_file()
    assert not plist.exists()
    assert harness.inspect().returncode != 0

    yield harness

    cleanup_failures = _cleanup_smoke(harness)
    if cleanup_failures:
        pytest.fail("; ".join(cleanup_failures))


def test_real_launchagent_install_status_remove(
    launchd_smoke: _SmokeHarness,
) -> None:
    disabled = launchd_smoke.inspect_disabled()
    installed = launchd_smoke.service("install")
    assert installed.returncode == 0, (
        f"install stdout:\n{installed.stdout}\ninstall stderr:\n{installed.stderr}\n"
        f"print-disabled rc={disabled.returncode}\n"
        f"print-disabled stdout:\n{disabled.stdout}\n"
        f"print-disabled stderr:\n{disabled.stderr}"
    )
    install_response = ServiceResponse.model_validate_json(installed.stdout)

    status = launchd_smoke.service("status")
    assert status.returncode == 0, status.stderr
    status_response = ServiceResponse.model_validate_json(status.stdout)
    with Store(launchd_smoke.database) as store:
        heartbeat = store.daemon.status()

    assert install_response.state == "installed"
    assert status_response.state == "active"
    assert status_response.enabled is True
    assert status_response.active is True
    assert status_response.heartbeat == "running"
    assert status_response.main_pid is not None
    assert status_response.main_pid == heartbeat.pid
    assert launchd_smoke.inspect().returncode == 0

    removed = launchd_smoke.service("remove")
    assert removed.returncode == 0, removed.stderr
    remove_response = ServiceResponse.model_validate_json(removed.stdout)
    assert remove_response.state == "removed"
    assert launchd_smoke.inspect().returncode != 0
    assert not launchd_smoke.plist.exists()
    assert launchd_smoke.database.exists()
