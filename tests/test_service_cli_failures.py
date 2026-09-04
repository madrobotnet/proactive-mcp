from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from proactive_mcp import cli
from proactive_mcp.store import Store
from tests.service_cli_support import (
    PID,
    FakeDaemonStatus,
    Harness,
    ServiceResponse,
    make_harness,
    parse_response,
    record_running,
    run_cli,
    sha256,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _profile_canaries(harness: Harness) -> tuple[Path, ...]:
    if not harness.database.exists():
        with Store(harness.database) as store:
            _ = store.daemon.status()
    config = harness.database.parent / "config.toml"
    oauth = harness.database.parent / "credentials" / "google-readonly-oauth.json"
    tombstone = oauth.with_name("google-readonly-oauth.state.json")
    oauth.parent.mkdir(parents=True, exist_ok=True)
    _ = config.write_text("[daemon]\n", encoding="utf-8")
    _ = oauth.write_text("oauth-canary", encoding="utf-8")
    _ = tombstone.write_text("tombstone-canary", encoding="utf-8")
    return (harness.database, config, oauth, tombstone)


def test_install_rolls_back_new_unit_when_enable_fails(tmp_path: Path) -> None:
    # Given: a user manager that rejects enable/start.
    harness = make_harness(tmp_path, fail_enable=True)

    # When: installation reaches the failing systemctl operation.
    result = run_cli(harness, "install")

    # Then: the typed failure leaves no unit or enabled state behind.
    response = parse_response(result)
    assert result.returncode == 2
    assert response.state == "failed"
    assert response.code == "command_failed"
    assert not harness.unit.exists()
    assert not harness.state.exists()


def test_install_refuses_to_overwrite_unmanaged_unit(tmp_path: Path) -> None:
    # Given: an existing same-name unit without the ownership marker.
    harness = make_harness(tmp_path)
    harness.unit.parent.mkdir(parents=True)
    _ = harness.unit.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
    before = harness.unit.read_bytes()

    # When: install is requested.
    result = run_cli(harness, "install")

    # Then: the foreign unit is unchanged and no service command ran.
    response = parse_response(result)
    assert result.returncode == 2
    assert response.state == "unmanaged"
    assert response.code == "unmanaged_unit"
    assert harness.unit.read_bytes() == before
    assert not harness.state.exists()


def test_install_rejects_database_control_characters_with_fixed_code(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    harness.env["PROACTIVE_DATABASE"] = f"{harness.database}\nExecStartPost=/bin/false"

    result = run_cli(harness, "install")

    parsed = parse_response(result)
    assert result.returncode == 2
    assert parsed.state == "failed"
    assert parsed.code == "invalid_value"
    assert not harness.unit.exists()


def test_non_linux_returns_typed_unsupported_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the CLI boundary is running on a non-Linux platform.
    monkeypatch.setattr(sys, "platform", "darwin")

    # When: service installation is requested.
    result = cli.main(["service", "install"])
    captured = capsys.readouterr()

    # Then: a closed machine-readable unsupported result is returned.
    response = ServiceResponse.model_validate_json(captured.out)
    assert result == 2
    assert response.state == "unsupported"
    assert response.code == "unsupported_platform"
    assert response.linger == "not_applicable"


def test_install_rolls_back_when_heartbeat_is_missing(tmp_path: Path) -> None:
    # Given: a manager that can start, but no daemon heartbeat has been recorded.
    harness = make_harness(
        tmp_path,
        heartbeat=FakeDaemonStatus(liveness="never_started", pid=None),
    )
    preserved = _profile_canaries(harness)
    before = tuple(sha256(path) for path in preserved)

    # When: installation starts the unit and then reads liveness.
    result = run_cli(harness, "install")

    # Then: missing heartbeat is a typed failure that rolls back unit and manager.
    response = parse_response(result)
    assert result.returncode == 2
    assert response.state == "failed"
    assert response.code == "heartbeat_unavailable"
    assert not harness.unit.exists()
    assert not harness.state.exists()
    assert tuple(sha256(path) for path in preserved) == before


def test_install_rolls_back_when_main_pid_does_not_match_heartbeat_pid(
    tmp_path: Path,
) -> None:
    # Given: systemd reports a different MainPID than the recorded heartbeat.
    harness = make_harness(tmp_path, main_pid=PID + 1)
    record_running(harness.database)
    preserved = _profile_canaries(harness)
    before = tuple(sha256(path) for path in preserved)

    # When: installation starts the unit and then compares process identities.
    result = run_cli(harness, "install")

    # Then: a PID mismatch is the same heartbeat failure and leaves profile bytes.
    response = parse_response(result)
    assert result.returncode == 2
    assert response.state == "failed"
    assert response.code == "heartbeat_unavailable"
    assert not harness.unit.exists()
    assert not harness.state.exists()
    assert tuple(sha256(path) for path in preserved) == before
