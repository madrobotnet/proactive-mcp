from __future__ import annotations

from typing import TYPE_CHECKING

from tests.service_cli_support import (
    PID,
    make_harness,
    parse_response,
    record_running,
    run_cli,
    sha256,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_install_is_idempotent(tmp_path: Path) -> None:
    # Given: an already installed managed service.
    harness = make_harness(tmp_path)
    record_running(harness.database)
    assert run_cli(harness, "install").returncode == 0
    before = harness.unit.read_bytes()

    # When: install is repeated.
    repeated = run_cli(harness, "install")

    # Then: the same unit remains active without changing its content.
    assert repeated.returncode == 0
    assert parse_response(repeated).state == "installed"
    assert harness.unit.read_bytes() == before


def test_status_reports_active_service_and_heartbeat(tmp_path: Path) -> None:
    # Given: an installed service with a matching active process and heartbeat.
    harness = make_harness(tmp_path)
    record_running(harness.database)
    assert run_cli(harness, "install").returncode == 0

    # When: status is queried through the CLI.
    result = run_cli(harness, "status")

    # Then: service-manager and persisted liveness agree.
    response = parse_response(result)
    assert result.returncode == 0
    assert response.state == "active"
    assert response.main_pid == PID
    assert response.heartbeat == "running"


def test_remove_preserves_database_config_oauth_and_other_profiles(
    tmp_path: Path,
) -> None:
    # Given: an installed unit and profile state outside the unit path.
    harness = make_harness(tmp_path)
    record_running(harness.database)
    config = harness.database.parent / "config.toml"
    oauth = harness.database.parent / "credentials" / "google-readonly-oauth.json"
    tombstone = oauth.with_name("google-readonly-oauth.state.json")
    unrelated = harness.root / "other-profile.json"
    oauth.parent.mkdir()
    _ = config.write_text("[daemon]\n", encoding="utf-8")
    _ = oauth.write_text("oauth-canary", encoding="utf-8")
    _ = tombstone.write_text("tombstone-canary", encoding="utf-8")
    _ = unrelated.write_text("other-profile-canary", encoding="utf-8")
    assert run_cli(harness, "install").returncode == 0
    preserved_paths = (harness.database, config, oauth, tombstone, unrelated)
    preserved = {path: sha256(path) for path in preserved_paths}

    # When: the managed service is removed.
    result = run_cli(harness, "remove")

    # Then: only the unit is gone and all profile state is byte-identical.
    assert result.returncode == 0
    assert parse_response(result).state == "removed"
    assert not harness.unit.exists()
    assert not harness.state.exists()
    assert {path: sha256(path) for path in preserved} == preserved


def test_remove_is_idempotent_when_unit_is_absent(tmp_path: Path) -> None:
    # Given: one remove already observed that no managed service exists.
    harness = make_harness(tmp_path)
    first = run_cli(harness, "remove")
    assert first.returncode == 0

    # When: remove is requested again.
    second = run_cli(harness, "remove")

    # Then: the repeated call reports the same harmless absent state.
    assert second.returncode == 0
    assert parse_response(first).state == "absent"
    assert parse_response(second).state == "absent"
