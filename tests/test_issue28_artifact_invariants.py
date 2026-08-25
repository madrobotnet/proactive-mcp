from __future__ import annotations

import hashlib
import os
import stat
import tomllib
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Final, cast

import anyio
import pytest
from google.oauth2.credentials import Credentials
from keyring.errors import NoKeyringError
from scripts.build_alpha_bundle import ManifestError, verify_bundle

from proactive_mcp.cli.service_unit import render_user_unit
from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES, CredentialStore
from proactive_mcp.sources.google_sync import (
    GoogleReadDependencies,
    GoogleSyncService,
    GoogleTransportError,
)
from proactive_mcp.store import Store
from proactive_mcp.store.migrations import load_migrations
from tests.memory_tools_stdio import memory_session
from tests.test_google_sync import (
    FailingGmailReader,
    FakeCalendarReader,
    FakeCredentials,
    calendar_result,
)

_PROJECT_ROOT: Final = Path(__file__).parents[1]
_MIGRATION_FILES: Final = (
    "001_foundation.sql",
    "002_memory_items.sql",
    "003_source_sync_state.sql",
    "004_memory_model_v2.sql",
    "005_situations.sql",
    "006_sqlite_consistency.sql",
    "007_delivery.sql",
    "008_runtime_ownership.sql",
    "009_security_hardening.sql",
    "010_gmail_diagnostics_and_receipt_replay.sql",
)
_UNAVAILABLE: Final = "synthetic-keyring-unavailable"
_DAILY_TOOL_COUNT: Final = 13
_SCHEDULED_TOOL_COUNT: Final = 3
_ACCESS_TOKEN: Final = "synthetic-access" + "-token"
_REFRESH_TOKEN: Final = "synthetic-refresh" + "-token"
_TOKEN_URI: Final = "https://oauth2.googleapis.invalid" + "/token"
_CLIENT_SECRET: Final = "synthetic-client" + "-secret"


class _UnavailableKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        raise NoKeyringError(_UNAVAILABLE)

    def set_password(
        self,
        service_name: str,
        username: str,
        password: str,
    ) -> None:
        del service_name, username, password
        raise NoKeyringError(_UNAVAILABLE)

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username
        raise NoKeyringError(_UNAVAILABLE)


def _staged_bundle(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "bundle"
    wheels = root / "wheels"
    wheels.mkdir(parents=True)
    wheel = wheels / "proactive_mcp-0.1-py3-none-any.whl"
    _ = wheel.write_bytes(b"synthetic-alpha-wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    _ = (root / "SHA256SUMS").write_text(
        f"{digest}  wheels/{wheel.name}\n",
        encoding="utf-8",
    )
    return root, wheel


def test_alpha_manifest_accepts_exact_checksums(tmp_path: Path) -> None:
    # Given: one staged wheel and its exact generated manifest.
    root, _wheel = _staged_bundle(tmp_path)
    # When/Then: the shipped verifier accepts exactly one manifest target.
    assert verify_bundle(root) == 1


def test_alpha_manifest_rejects_checksum_mutation(tmp_path: Path) -> None:
    # Given: a valid staged bundle whose checksummed bytes are changed.
    root, wheel = _staged_bundle(tmp_path)
    _ = wheel.write_bytes(b"mutated-alpha-wheel")
    # When/Then: verification rejects the mutation before installation.
    with pytest.raises(ManifestError) as caught:
        _ = verify_bundle(root)
    assert caught.value.reason == "checksum-mismatch"


def test_alpha_manifest_rejects_target_mutation(tmp_path: Path) -> None:
    # Given: a valid manifest whose wheel target is renamed after generation.
    root, wheel = _staged_bundle(tmp_path)
    _ = wheel.rename(wheel.with_name("renamed-0.1-py3-none-any.whl"))
    # When/Then: verification rejects the changed manifest layout.
    with pytest.raises(ManifestError) as caught:
        _ = verify_bundle(root)
    assert caught.value.reason == "manifest-layout-mismatch"


def test_python_floor_scopes_and_latest_migration_remain_exact(tmp_path: Path) -> None:
    # Given: shipped project metadata, OAuth scopes, and packaged migrations.
    project = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
    with Store(tmp_path / "migration.db") as store:
        applied = store.status().migration_version
    # When/Then: all closed compatibility values retain their alpha contract.
    assert project["project"]["requires-python"] == ">=3.11"
    assert GOOGLE_READONLY_SCOPES == (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    )
    migrations = load_migrations()
    migration_files = tuple(
        sorted(
            resource.name
            for resource in files("proactive_mcp.store.migrations").iterdir()
            if resource.name.endswith(".sql")
        )
    )
    assert tuple(number for number, _sql in migrations) == tuple(range(1, 11))
    assert migration_files == _MIGRATION_FILES
    assert applied == 10


def test_posix_store_and_oauth_fallback_remain_private(tmp_path: Path) -> None:
    # Given: a cold state root and unavailable keyring force the OAuth file adapter.
    state = tmp_path / "state"
    database = state / "proactive.db"
    with Store(database):
        credential_store = CredentialStore(state, keyring=_UnavailableKeyring())
        credential_store.save(
            Credentials(
                token=_ACCESS_TOKEN,
                refresh_token=_REFRESH_TOKEN,
                token_uri=_TOKEN_URI,
                client_id="synthetic-client-id",
                client_secret=_CLIENT_SECRET,
                scopes=list(GOOGLE_READONLY_SCOPES),
            )
        )
    # When/Then: state directories are 0700 and persisted private files are 0600.
    if os.name == "nt":
        assert state.is_dir()
        assert database.is_file()
        assert credential_store.file_path.parent.is_dir()
        assert credential_store.file_path.is_file()
        return
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(credential_store.file_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(credential_store.file_path.stat().st_mode) == 0o600


def test_calendar_health_remains_independent_from_gmail_failure(tmp_path: Path) -> None:
    # Given: Gmail transport fails while Calendar returns a complete projection.
    with Store(tmp_path / "independent.db") as store:
        summary = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FailingGmailReader(GoogleTransportError("network")),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        ).sync()
        gmail, calendar = store.list_source_sync()
    # When/Then: Calendar remains healthy independently of Gmail diagnostics.
    assert summary.gmail_diagnostics.outcome == "transport_error"
    assert gmail.last_error_code == "network"
    assert calendar.last_error_code is None
    assert calendar.last_success_at is not None


def test_systemd_unit_keeps_restart_and_lifecycle_tokens() -> None:
    # Given: the shipped unit renderer with absolute executable and database paths.
    unit = render_user_unit(
        cast(
            "Path",
            cast("object", PurePosixPath("/opt/proactive/bin/proactive-mcp")),
        ),
        cast(
            "Path",
            cast("object", PurePosixPath("/var/lib/proactive/proactive.db")),
        ),
    )
    lines = set(unit.splitlines())
    # When/Then: readiness, restart, hardening, and lifecycle tokens remain exact.
    assert {
        "Type=notify",
        "Restart=on-failure",
        "RestartPreventExitStatus=2",
        "NotifyAccess=main",
        "UMask=0077",
        "WantedBy=default.target",
    } <= lines
    assert any(
        line.startswith("ExecStart=/") and line.endswith(" daemon") for line in lines
    )


@pytest.mark.anyio
async def test_session_profiles_keep_counts_and_machine_routing(tmp_path: Path) -> None:
    # Given: both shipped MCP profiles over their real stdio adapters.
    with anyio.fail_after(20):
        async with memory_session(
            tmp_path / "daily",
            server_args=("-m", "proactive_mcp", "serve"),
        ) as daily:
            daily_tools = await daily.list_tools()
        async with memory_session(
            tmp_path / "scheduled",
            server_args=("-m", "proactive_mcp", "serve-scheduled"),
        ) as scheduled:
            scheduled_tools = await scheduled.list_tools()
    # When/Then: counts and machine-consumed session routing remain exact.
    assert len(daily_tools.tools) == _DAILY_TOOL_COUNT
    assert len(scheduled_tools.tools) == _SCHEDULED_TOOL_COUNT
    scheduled_by_name = {tool.name: tool for tool in scheduled_tools.tools}
    assert scheduled_by_name["proactive_check"].meta == {
        "session_contract": "one_check"
    }
    assert scheduled_by_name["confirm_delivery"].meta == {
        "session_contract": "conditional_confirm"
    }
