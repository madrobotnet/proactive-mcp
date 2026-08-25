import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from proactive_mcp import cli
from proactive_mcp.server import StatusResponse
from proactive_mcp.server.situation_responses import SourceReadDiagnosticsResponse
from proactive_mcp.sources import (
    GoogleOAuthAuthorizationTimeoutError,
    GoogleOAuthAuthorizer,
    GoogleReadDependencies,
    GoogleReadSummary,
    GoogleSetupOptions,
    GoogleSyncService,
)
from proactive_mcp.sources.credentials import CredentialStore
from proactive_mcp.store import SourceErrorCode, Store
from tests.test_google_oauth import (
    FIXTURES,
    FakeFlowFactory,
    FakeInstalledAppFlow,
    FakeKeyring,
    TimeoutInstalledAppFlow,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)
from tests.test_google_sync import (
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)


class _LegacySmokeSource(BaseModel):
    """Pre-Todo-7 source parser used to prove additive compatibility."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    error_code: SourceErrorCode | None


class _LegacySmokeResponse(BaseModel):
    """Pre-Todo-7 smoke parser, which ignores additive fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: _LegacySmokeSource
    calendar: _LegacySmokeSource
    credential_cleanup_failed: bool


class _CurrentSmokeGmail(_LegacySmokeSource):
    """Typed additive Gmail smoke shape used for privacy assertions."""

    diagnostics: SourceReadDiagnosticsResponse


class _CurrentSmokeResponse(BaseModel):
    """Typed current smoke response preserving every legacy field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: _CurrentSmokeGmail
    calendar: _LegacySmokeSource
    credential_cleanup_failed: bool


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "proactive_mcp", *args]
    return subprocess.run(command, capture_output=True, text=True, env=env, check=False)


def test_help_is_useful() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "serve" in result.stdout
    assert "status" in result.stdout
    assert "setup" in result.stdout
    assert "disconnect" in result.stdout
    assert "google-smoke" in result.stdout
    assert "daemon" in result.stdout


def test_status_prints_server_status_contract(tmp_path: Path) -> None:
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "status.db")}
    result = run_cli("status", env=env)

    assert result.returncode == 0
    status = StatusResponse.model_validate_json(result.stdout)
    assert status.database.status == "healthy"
    assert status.google.gmail.status == "not_configured"
    assert status.google.gmail.last_success_at is None
    assert status.google.gmail.last_attempt_at is None
    assert status.google.gmail.age_seconds is None
    assert status.google.gmail.error_code is None
    assert status.google.calendar.status == "not_configured"
    assert status.daemon.status == "not_running"
    assert status.overall == "degraded"
    assert status.warnings


def test_setup_help_exposes_reauthorization_and_headless_controls() -> None:
    result = run_cli("setup", "--help")

    assert result.returncode == 0
    assert "--reauth" in result.stdout
    assert "--headless" in result.stdout
    assert "--client-secrets" in result.stdout


def test_setup_prefers_explicit_client_secrets_over_environment_and_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: all supported client-secret locations have distinct paths.
    database_path = tmp_path / "state" / "proactive.db"
    explicit_path = tmp_path / "explicit.json"
    environment_path = tmp_path / "environment.json"
    configured: list[tuple[Path, bool, bool]] = []
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(environment_path))

    def configure(path: Path, options: GoogleSetupOptions) -> None:
        assert path == database_path
        configured.append(
            (options.client_secrets_path, options.reauth, options.headless)
        )

    monkeypatch.setattr(cli, "configure_google_sources", configure)

    # When: setup receives an explicit client-secret path.
    result = cli.main(
        [
            "setup",
            "--client-secrets",
            str(explicit_path),
            "--reauth",
            "--headless",
        ]
    )

    # Then: setup selects only the explicit path and forwards its controls.
    assert result == 0
    assert configured == [(explicit_path, True, True)]


def test_disconnect_deletes_google_authorization_for_selected_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a selected proactive database and a captured disconnect boundary.
    database_path = tmp_path / "state" / "proactive.db"
    disconnected: list[Path] = []
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setattr(cli, "disconnect_google_sources", disconnected.append)

    # When: the operator runs the credential-first rollback command.
    result = cli.main(["disconnect"])
    captured = capsys.readouterr()

    # Then: only that state root is disconnected and success is machine-readable.
    assert result == 0
    assert disconnected == [database_path]
    assert json.loads(captured.out) == {"google": "disconnected"}
    assert captured.err == ""


def test_setup_reports_a_safe_error_for_invalid_client_secrets(tmp_path: Path) -> None:
    # Given: a nonexistent client-secret path.
    missing_path = tmp_path / "contains-secret.json"
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "state.db")}

    # When: setup attempts to parse the path.
    result = run_cli("setup", "--client-secrets", str(missing_path), env=env)

    # Then: the error is actionable but does not disclose the input path.
    assert result.returncode != 0
    assert result.stderr
    assert str(missing_path) not in result.stderr


def test_setup_reports_authorization_timeout_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the bounded Google loopback flow expires before authorization.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))

    def timeout(_path: Path, _options: GoogleSetupOptions) -> None:
        raise GoogleOAuthAuthorizationTimeoutError

    monkeypatch.setattr(cli, "configure_google_sources", timeout)

    # When: setup reaches the CLI error boundary.
    result = cli.main(["setup", "--client-secrets", str(tmp_path / "client.json")])
    captured = capsys.readouterr()

    # Then: the command fails safely without exposing an exception traceback.
    assert result == 2
    assert captured.out == ""
    assert captured.err
    assert "Traceback" not in captured.err


def test_google_smoke_requires_explicit_real_account_read_opt_in(
    tmp_path: Path,
) -> None:
    # Given: an isolated state directory without credentials.
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "state.db")}

    # When: the smoke command omits its confirmation flag.
    result = run_cli("google-smoke", env=env)

    # Then: it fails before loading credentials or reading Google data.
    assert result.returncode != 0
    assert result.stderr


def test_google_smoke_adds_typed_counts_without_leaking_source_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a safe-truncated read seeded with every forbidden value class.
    forbidden = (
        "message-canary",
        "person@example.test",
        "/private/path-canary",
        "oauth-token-canary",
        "exception-text-canary",
        "thread-two-canary",
        "message-two-canary",
    )
    base = gmail_inbox_result()
    thread = replace(
        base.threads[0],
        thread_id=forbidden[0],
        latest_message_id=forbidden[1],
        subject=forbidden[2],
        sender_display=forbidden[3],
        body_text=forbidden[4],
    )
    second = replace(
        thread,
        thread_id=forbidden[5],
        latest_message_id=forbidden[6],
    )
    gmail_result = replace(
        base,
        threads=(thread, second),
        provider_history_cursor="traceback-canary",
        degradation_reasons=("body_truncated",),
        request_count=4,
        page_count=2,
        projected_thread_count=2,
        excluded_thread_count=2,
        degradation_reason_counts=(("body_truncated", 2),),
    )
    with Store(tmp_path / "proactive.db") as store:
        summary = GoogleSyncService(
            GoogleReadDependencies(
                store=store,
                gmail=FakeInboxReader(gmail_result),
                calendar=FakeCalendarReader(calendar_result()),
                credentials=FakeCredentials(),
            )
        ).sync()
    def read_smoke(_path: Path, *, enabled: bool) -> GoogleReadSummary:
        assert enabled
        return summary

    monkeypatch.setattr(cli, "run_google_read_smoke", read_smoke)

    # When: the opt-in smoke serializer emits the additive document.
    result = cli.main(["google-smoke", "--confirm-real-account-read"])
    captured = capsys.readouterr()

    # Then: old parsers still work and diagnostics contain only closed counts.
    assert result == 0
    legacy = _LegacySmokeResponse.model_validate_json(captured.out)
    assert legacy.gmail.count == 2
    assert legacy.gmail.error_code is None
    current = _CurrentSmokeResponse.model_validate_json(captured.out)
    diagnostics = current.gmail.diagnostics.model_dump()
    assert diagnostics == {
        "outcome": "healthy",
        "request_count": 4,
        "page_count": 2,
        "projected_count": 2,
        "excluded_count": 2,
        "byte_budget": 8_000_000,
        "reason_counts": {"body_truncated": 2},
    }
    assert "path" not in diagnostics
    assert all(value not in captured.out for value in (*forbidden, "traceback-canary"))


def test_google_smoke_reports_missing_storedgoogle_credential(tmp_path: Path) -> None:
    # Given: an isolated state directory and a guaranteed unavailable keyring.
    env = os.environ | {
        "PROACTIVE_DATABASE": str(tmp_path / "state.db"),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
    }

    # When: the explicitly enabled smoke command has no stored credential.
    result = run_cli("google-smoke", "--confirm-real-account-read", env=env)

    # Then: it fails clearly without attempting a network read.
    assert result.returncode != 0
    assert result.stderr


def test_invalid_command_prints_usage_and_fails() -> None:
    result = run_cli("definitely-invalid")

    assert result.returncode != 0
    assert "usage:" in result.stderr.lower()


def test_serve_delegates_to_official_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli, "run_server", lambda: called.append(True))

    assert cli.main(["serve"]) == 0
    assert called == [True]


def test_serve_scheduled_delegates_to_restricted_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cli, "run_scheduled_server", lambda: called.append(True))

    assert cli.main(["serve-scheduled"]) == 0
    assert called == [True]


def test_codex_docs_never_auto_approve_the_full_server() -> None:
    integrations = (Path(__file__).parents[1] / "docs" / "INTEGRATIONS.md").read_text(
        encoding="utf-8"
    )

    unsafe_override = "mcp_servers.proactive.default_tools_approval_mode=approve"
    assert unsafe_override not in integrations
    assert "[mcp_servers.proactive_scheduled]" in integrations
    assert '"serve-scheduled"' in integrations


def _install_fake_authorizer(
    monkeypatch: pytest.MonkeyPatch, flow: FakeInstalledAppFlow
) -> None:
    factory = FakeFlowFactory(flow)

    def build(store: CredentialStore) -> GoogleOAuthAuthorizer:
        return GoogleOAuthAuthorizer(store, flow_factory=factory)

    def credential_store(path: Path) -> CredentialStore:
        return CredentialStore(path, keyring=FakeKeyring())

    monkeypatch.setattr("proactive_mcp.sources.GoogleOAuthAuthorizer", build)
    monkeypatch.setattr("proactive_mcp.sources.CredentialStore", credential_store)


def test_headless_setup_emits_single_url_and_success_when_authorization_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: setup --headless with an injected library-like loopback flow.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    # When: the real CLI boundary completes authorization.
    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    # Then: the CLI owns exactly one URL event and one success event.
    assert result == 0
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 1


def test_headless_setup_emits_no_success_when_authorization_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the injected loopback flow expires before consent.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, TimeoutInstalledAppFlow(google_credential()))

    # When: setup --headless reaches the CLI error boundary.
    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    # Then: success is absent, URL is at most one, and the error stays safe.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert captured.err
    assert "Traceback" not in captured.err
    assert str(FIXTURES / "installed-client.json") not in captured.err


def test_headless_setup_emits_no_success_when_client_config_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a malformed client-secret file.
    invalid_path = tmp_path / "client.json"
    _ = invalid_path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    # When: setup --headless parses the untrusted file.
    result = cli.main(
        ["setup", "--headless", "--client-secrets", str(invalid_path)]
    )
    captured = capsys.readouterr()

    # Then: neither a URL nor a success event is emitted.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) == 0
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert str(invalid_path) not in captured.err
    assert "Traceback" not in captured.err


def test_headless_setup_emits_no_success_when_refresh_token_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the flow completes without a durable refresh token.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(
        monkeypatch, FakeInstalledAppFlow(google_credential(refresh_token=None))
    )

    # When: setup --headless tries to persist the credential.
    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    # Then: failure cannot look like success.
    assert result == 2
    assert count_authorization_url_events(captured.out, captured.err) <= 1
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert "Traceback" not in captured.err


def test_headless_setup_hides_untrusted_client_endpoints_when_authorization_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a valid installed-app shape that points at attacker endpoints.
    client_file = tmp_path / "installed-client.json"
    _ = client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client.apps.googleusercontent.com",
                    "client_secret": "sanitized-test-client-secret",
                    "auth_uri": "https://accounts.google.com@attacker.invalid/auth",
                    "token_uri": "http://127.0.0.1:8080/token",
                    "redirect_uris": ["https://attacker.invalid/callback"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    # When: setup --headless authorizes with the untrusted file.
    result = cli.main(
        ["setup", "--headless", "--client-secrets", str(client_file)]
    )
    captured = capsys.readouterr()

    # Then: output stays single-owned and does not echo attacker hosts.
    assert result == 0
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 1
    assert "attacker.invalid" not in captured.out
    assert "attacker.invalid" not in captured.err
