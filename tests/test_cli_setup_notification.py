"""Hermetic CLI tests for the interactive setup test OS notification."""

from __future__ import annotations

import inspect
import io
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import pytest
from typing_extensions import override

import proactive_mcp.delivery.notify as notify_module
from proactive_mcp import cli
from proactive_mcp.cli import setup_notification
from proactive_mcp.delivery.notify import SubprocessNotificationRunner
from proactive_mcp.sources import GoogleOAuthAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from proactive_mcp.sources import GoogleSetupOptions

PROMPT: Final = "prompt"
OAUTH_SUCCESS: Final = "oauth_success"
OAUTH_FAILURE: Final = "oauth_failure"
SERVICE_INSTALL: Final = "service:install"
NOTIFY: Final = "notify"
_NON_INTERACTIVE_PROMPT_ERROR: Final = "non-interactive setup must not prompt for input"
_EXPECTED_TITLE: Final = "proactive-mcp"
_EXPECTED_BODY: Final = "Setup test notification"
_CANARY_GMAIL: Final = "CANARY_GMAIL_alice.secret@corp.example"
_CANARY_SITUATION: Final = "CANARY_SIT_Q3-layoff-list"
_CANARY_CALENDAR: Final = "CANARY_EVENT_stealth-acquisition"
_CANARY_TYPE: Final = "reply_deadline"
_CANARY_PATH: Final = "CANARY_USER_alice"
_SENSITIVE_STDERR: Final = f"stderr:{_CANARY_GMAIL}:{_CANARY_SITUATION}"


@dataclass(frozen=True, slots=True)
class _Linger:
    linger: Literal["enabled", "disabled"] = "enabled"


@dataclass(frozen=True, slots=True)
class _ServiceResult:
    success: bool
    response: _Linger = _Linger()


class _DeterministicTTYStringIO(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


class _EventTTYStringIO(_DeterministicTTYStringIO):
    def __init__(self, value: str, events: list[str]) -> None:
        super().__init__(value)
        self._events: list[str] = events

    @override
    def readline(self, size: int = -1) -> str:
        self._events.append(PROMPT)
        return super().readline(size)


class _NoPromptTTYStringIO(_DeterministicTTYStringIO):
    @override
    def readline(self, _size: int = -1) -> str:
        raise AssertionError(_NON_INTERACTIVE_PROMPT_ERROR)


class _RecordingRunner:
    """Collect argv vectors; mutation is the test probe."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], timedelta]] = []

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        self.calls.append((tuple(argv), timeout))


@dataclass(frozen=True, slots=True)
class _Session:
    events: list[str]
    recorder: _RecordingRunner
    client_path: Path
    database_path: Path


@dataclass(frozen=True, slots=True)
class _SessionSpec:
    platform: str
    service: _ServiceResult | None = None
    configure_error: Exception | None = None


def _assert_no_canaries(text: str) -> None:
    assert _CANARY_GMAIL not in text
    assert _CANARY_SITUATION not in text
    assert _CANARY_CALENDAR not in text
    assert _CANARY_TYPE not in text
    assert _CANARY_PATH not in text
    assert _SENSITIVE_STDERR not in text


def _canary_path(root: Path, name: str) -> Path:
    return root / _CANARY_PATH / name


def _delivery_dir() -> Path:
    path = notify_module.__file__
    assert path is not None
    return Path(path).resolve().parent


@pytest.fixture
def recording_runner(monkeypatch: pytest.MonkeyPatch) -> _RecordingRunner:
    runner = _RecordingRunner()

    def factory() -> _RecordingRunner:
        return runner

    monkeypatch.setattr(setup_notification, "SubprocessNotificationRunner", factory)
    monkeypatch.setattr(setup_notification, "notification_available", lambda: True)
    monkeypatch.setattr(
        notify_module,
        "_windows_system_directory",
        lambda: r"C:\Windows\System32",
    )
    return runner


def _begin_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recording_runner: _RecordingRunner,
    spec: _SessionSpec,
) -> _Session:
    events: list[str] = []
    client_path = _canary_path(tmp_path, "client-secret.json")
    database_path = _canary_path(tmp_path, "state") / "proactive.db"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    _ = client_path.write_text(_CANARY_GMAIL, encoding="utf-8")
    monkeypatch.setattr(sys, "platform", spec.platform)
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setattr("sys.stdout", io.StringIO())

    def configure(_path: Path, _options: GoogleSetupOptions) -> None:
        if spec.configure_error is not None:
            events.append(OAUTH_FAILURE)
            raise spec.configure_error
        events.append(OAUTH_SUCCESS)

    def execute_service(_action: str) -> _ServiceResult:
        events.append(SERVICE_INSTALL)
        return _ServiceResult(success=True) if spec.service is None else spec.service

    original_run = recording_runner.run

    def run(argv: Sequence[str], timeout: timedelta) -> None:
        events.append(NOTIFY)
        original_run(argv, timeout)

    answers = _EventTTYStringIO(f"{client_path}\ny\nn\n", events)
    monkeypatch.setattr(recording_runner, "run", run)
    monkeypatch.setattr(cli, "configure_google_sources", configure)
    monkeypatch.setattr(cli, "execute_service", execute_service)
    monkeypatch.setattr("sys.stdin", answers)
    return _Session(events, recording_runner, client_path, database_path)


def _bind_answers(
    monkeypatch: pytest.MonkeyPatch, session: _Session, answers: str
) -> None:
    monkeypatch.setattr("sys.stdin", _EventTTYStringIO(answers, session.events))


def _expected_argv(platform: str) -> tuple[str, ...]:
    match platform:
        case "linux":
            return (
                "/usr/bin/notify-send",
                "--",
                _EXPECTED_TITLE,
                _EXPECTED_BODY,
            )
        case "darwin":
            return (
                "/usr/bin/osascript",
                str(_delivery_dir() / "macos_notification.applescript"),
                _EXPECTED_TITLE,
                _EXPECTED_BODY,
            )
        case "win32":
            return (
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(_delivery_dir() / "windows_toast.ps1"),
                _EXPECTED_TITLE,
                _EXPECTED_BODY,
            )
        case unreachable:
            raise AssertionError(unreachable)


def test_send_setup_test_notification_accepts_no_user_data() -> None:
    parameters = inspect.signature(setup_notification.send_setup_test_notification)
    assert list(parameters.parameters) == []


def test_interactive_linux_declining_service_notifies_once_after_oauth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert session.events == [PROMPT, PROMPT, OAUTH_SUCCESS, PROMPT, NOTIFY]
    assert SERVICE_INSTALL not in session.events
    assert len(recording_runner.calls) == 1


def test_interactive_linux_service_success_notifies_after_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )
    _bind_answers(monkeypatch, session, f"{session.client_path}\ny\ny\n")

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert session.events == [
        PROMPT,
        PROMPT,
        OAUTH_SUCCESS,
        PROMPT,
        SERVICE_INSTALL,
        NOTIFY,
    ]
    assert len(recording_runner.calls) == 1


def test_interactive_linux_service_failure_skips_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch,
        tmp_path,
        recording_runner,
        _SessionSpec("linux", service=_ServiceResult(success=False)),
    )
    _bind_answers(monkeypatch, session, f"{session.client_path}\ny\ny\n")

    exit_code = cli.main(["setup"])

    assert exit_code == 2
    assert SERVICE_INSTALL in session.events
    assert NOTIFY not in session.events
    assert recording_runner.calls == []


def test_interactive_oauth_failure_skips_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch,
        tmp_path,
        recording_runner,
        _SessionSpec("linux", configure_error=GoogleOAuthAuthorizationError()),
    )

    exit_code = cli.main(["setup"])

    assert exit_code == 2
    assert session.events == [PROMPT, PROMPT, OAUTH_FAILURE]
    assert NOTIFY not in session.events
    assert recording_runner.calls == []


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_interactive_non_linux_notifies_without_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
    platform: str,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec(platform)
    )

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert session.events == [PROMPT, PROMPT, OAUTH_SUCCESS, NOTIFY]
    assert SERVICE_INSTALL not in session.events
    assert len(recording_runner.calls) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["setup", "--non-interactive"],
        ["setup", "--headless"],
        ["setup", "--reauth"],
    ],
)
def test_legacy_and_non_interactive_flags_skip_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
    argv: list[str],
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )
    monkeypatch.setattr("sys.stdin", _NoPromptTTYStringIO())

    exit_code = cli.main(argv)

    assert exit_code == 0
    assert session.events == [OAUTH_SUCCESS]
    assert recording_runner.calls == []


def test_client_secrets_flag_skips_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )
    monkeypatch.setattr("sys.stdin", _NoPromptTTYStringIO())

    exit_code = cli.main(["setup", "--client-secrets", str(session.client_path)])

    assert exit_code == 0
    assert session.events == [OAUTH_SUCCESS]
    assert recording_runner.calls == []


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_setup_notification_argv_is_fixed_and_excludes_canaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
    platform: str,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec(platform)
    )

    def situation_payload(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(_CANARY_SITUATION)

    monkeypatch.setattr(
        "proactive_mcp.delivery.payload.notification_payload",
        situation_payload,
    )

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert len(recording_runner.calls) == 1
    argv, timeout = recording_runner.calls[0]
    assert argv == _expected_argv(platform)
    assert timeout == timedelta(seconds=5)
    joined = "\0".join(argv)
    _assert_no_canaries(joined)
    assert str(session.database_path) not in joined
    assert str(session.client_path) not in joined


def test_successful_rerun_sends_once_per_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )

    first = cli.main(["setup"])
    _bind_answers(monkeypatch, session, f"{session.client_path}\ny\nn\n")
    second = cli.main(["setup"])

    assert first == 0
    assert second == 0
    assert session.events.count(NOTIFY) == 2
    assert len(recording_runner.calls) == 2


def test_setup_notification_does_not_write_database_or_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_runner: _RecordingRunner,
) -> None:
    session = _begin_session(
        monkeypatch, tmp_path, recording_runner, _SessionSpec("linux")
    )

    def connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        message = "setup notification opened sqlite"
        raise AssertionError(message)

    monkeypatch.setattr(sqlite3, "connect", connect)

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert len(recording_runner.calls) == 1
    assert not session.database_path.exists()


def _missing_notifier(
    _argv: Sequence[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError(2, _SENSITIVE_STDERR, _CANARY_PATH)


def _nonzero_notifier(
    argv: Sequence[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        list(argv), 1, _SENSITIVE_STDERR, _SENSITIVE_STDERR
    )


def _timeout_notifier(
    argv: Sequence[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    raise subprocess.TimeoutExpired(
        cmd=argv,
        timeout=5,
        output=_CANARY_CALENDAR.encode(),
        stderr=_SENSITIVE_STDERR.encode(),
    )


def _permission_notifier(
    _argv: Sequence[str], **_kwargs: object
) -> subprocess.CompletedProcess[str]:
    raise PermissionError(13, _SENSITIVE_STDERR, f"/secret/{_CANARY_PATH}")


def _bind_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run: object,
) -> None:
    monkeypatch.setattr(setup_notification, "notification_available", lambda: True)
    monkeypatch.setattr(
        setup_notification,
        "SubprocessNotificationRunner",
        SubprocessNotificationRunner,
    )
    monkeypatch.setattr(subprocess, "run", run)
    session = _begin_session(
        monkeypatch,
        tmp_path,
        _RecordingRunner(),
        _SessionSpec("linux"),
    )
    _bind_answers(monkeypatch, session, f"{session.client_path}\ny\nn\n")


@pytest.mark.parametrize(
    ("run", "code"),
    [
        (_missing_notifier, "unavailable"),
        (_nonzero_notifier, "failed"),
        (_timeout_notifier, "timeout"),
        (_permission_notifier, "failed"),
    ],
)
def test_transport_failures_are_advisory_redacted_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run: object,
    code: str,
) -> None:
    _bind_transport(monkeypatch, tmp_path, run)

    exit_code = cli.main(["setup"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert exit_code == 0
    assert captured.err.split()[-1] == code
    assert "Traceback" not in combined
    _assert_no_canaries(combined)
