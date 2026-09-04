"""Hermetic issue #43 setup-to-service handoff tests using event logs."""

from __future__ import annotations

import getpass
import io
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import pytest
from typing_extensions import override

from proactive_mcp import cli
from proactive_mcp.cli import service as service_cli
from proactive_mcp.cli.service_models import ServiceAction, ServiceResponse
from proactive_mcp.sources import GoogleOAuthAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from proactive_mcp.sources import GoogleSetupOptions

PROMPT: Final = "prompt"
OAUTH_SUCCESS: Final = "oauth_success"
OAUTH_FAILURE: Final = "oauth_failure"
SERVICE_INSTALL: Final = "service:install"
RUN_SERVICE: Final = "run_service"
SUBPROCESS_CLI: Final = "subprocess:service-install"
_UNIT: Final = "proactive-mcp.service"
_OAUTH_CANARY: Final = "oauth-canary"
_NON_INTERACTIVE_PROMPT_ERROR: Final = "non-interactive setup must not prompt for input"


@dataclass(frozen=True, slots=True)
class _ServiceCommandResult:
    response: ServiceResponse
    success: bool


@dataclass(frozen=True, slots=True)
class _SetupSession:
    events: list[str]
    client_path: Path
    oauth_canary: Path
    unit_path: Path


class _DeterministicTTYStringIO(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


class _EventTTYStringIO(_DeterministicTTYStringIO):
    def __init__(
        self, value: str, events: list[str], interrupt_at: int | None = None
    ) -> None:
        super().__init__(value)
        self._events: list[str] = events
        self._interrupt_at: int | None = interrupt_at

    @override
    def readline(self, size: int = -1) -> str:
        self._events.append(PROMPT)
        if self._events.count(PROMPT) == self._interrupt_at:
            raise KeyboardInterrupt
        return super().readline(size)


class _NoPromptTTYStringIO(_DeterministicTTYStringIO):
    @override
    def readline(self, _size: int = -1) -> str:
        raise AssertionError(_NON_INTERACTIVE_PROMPT_ERROR)


def _installed_result(
    *, linger: Literal["enabled", "disabled"] = "enabled"
) -> _ServiceCommandResult:
    return _ServiceCommandResult(
        response=ServiceResponse(
            action="install",
            state="installed",
            unit=_UNIT,
            managed=True,
            enabled=True,
            active=True,
            main_pid=1,
            heartbeat="running",
            linger=linger,
            guidance="enable_linger" if linger == "disabled" else "none",
            code=None,
        ),
        success=True,
    )


def _failed_result() -> _ServiceCommandResult:
    return _ServiceCommandResult(
        response=ServiceResponse(
            action="install",
            state="failed",
            unit=_UNIT,
            managed=False,
            enabled=False,
            active=False,
            main_pid=None,
            heartbeat=None,
            linger="unknown",
            guidance="none",
            code="command_failed",
        ),
        success=False,
    )


def _begin_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: _ServiceCommandResult | None = None,
) -> _SetupSession:
    events: list[str] = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    client_path = tmp_path / "client-secret.json"
    oauth_canary = tmp_path / "state" / "oauth-canary"
    xdg_home = tmp_path / "xdg"
    _ = client_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state" / "proactive.db"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    core_result = _installed_result() if result is None else result

    def configure(_path: Path, _options: GoogleSetupOptions) -> None:
        oauth_canary.parent.mkdir(parents=True, exist_ok=True)
        _ = oauth_canary.write_text(_OAUTH_CANARY, encoding="utf-8")
        events.append(OAUTH_SUCCESS)

    def execute_service(action: ServiceAction) -> _ServiceCommandResult:
        events.append(f"service:{action}")
        return core_result

    original_run_service = service_cli.run_service

    def run_service(action: ServiceAction) -> int:
        events.append(RUN_SERVICE)
        return original_run_service(action)

    monkeypatch.setattr(cli, "configure_google_sources", configure)
    monkeypatch.setattr(cli, "execute_service", execute_service, raising=False)
    monkeypatch.setattr(service_cli, "execute_service", execute_service, raising=False)
    monkeypatch.setattr(cli, "run_service", run_service)
    monkeypatch.setattr(service_cli, "run_service", run_service)
    return _SetupSession(
        events, client_path, oauth_canary, xdg_home / "systemd" / "user" / _UNIT
    )


def _bind_tty(monkeypatch: pytest.MonkeyPatch, events: list[str], answers: str) -> None:
    monkeypatch.setattr("sys.stdin", _EventTTYStringIO(answers, events))


def _answers(session: _SetupSession, browser: str, service: str) -> str:
    return f"{session.client_path}\n{browser}\n{service}\n"


def test_setup_prompts_for_service_once_after_oauth_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: interactive setup will complete OAuth and decline service install.
    session = _begin_session(monkeypatch, tmp_path)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "n"))

    # When: the user finishes the wizard through the service consent question.
    exit_code = cli.main(["setup"])

    # Then: exactly one prompt is issued after OAuth success, and only once.
    assert exit_code == 0
    assert session.events == [PROMPT, PROMPT, OAUTH_SUCCESS, PROMPT]


def test_setup_does_not_prompt_before_oauth_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: interactive setup still has unused service-consent input queued.
    session = _begin_session(monkeypatch, tmp_path)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "y"))

    # When: OAuth configuration runs.
    exit_code = cli.main(["setup"])

    # Then: only the two wizard prompts exist before OAuth success.
    oauth_at = session.events.index(OAUTH_SUCCESS)
    assert exit_code == 0
    assert session.events[:oauth_at] == [PROMPT, PROMPT]
    assert SERVICE_INSTALL not in session.events[:oauth_at]


def test_setup_no_and_non_interactive_do_not_touch_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an interactive user who answers no after OAuth.
    declined = _begin_session(monkeypatch, tmp_path / "no")
    _bind_tty(monkeypatch, declined.events, _answers(declined, "y", "n"))

    # When: interactive setup completes with an explicit no.
    declined_code = cli.main(["setup"])

    # Then: the service core is not invoked and no unit file is written.
    assert declined_code == 0
    assert SERVICE_INSTALL not in declined.events
    assert RUN_SERVICE not in declined.events
    assert not declined.unit_path.exists()

    # Given: a non-interactive automation path with no service flag.
    silent = _begin_session(monkeypatch, tmp_path / "ni")
    monkeypatch.setattr("sys.stdin", _NoPromptTTYStringIO())

    # When: setup runs with --non-interactive.
    silent_code = cli.main(
        [
            "setup",
            "--non-interactive",
            "--headless",
            "--client-secrets",
            str(silent.client_path),
        ]
    )

    # Then: there is no prompt and service state is untouched.
    assert silent_code == 0
    assert silent.events == [OAUTH_SUCCESS]
    assert not silent.unit_path.exists()


def test_setup_oauth_failure_does_not_prompt_for_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: Google authorization will fail after the wizard prompts.
    session = _begin_session(monkeypatch, tmp_path)

    def configure(_path: Path, _options: GoogleSetupOptions) -> None:
        session.events.append(OAUTH_FAILURE)
        raise GoogleOAuthAuthorizationError

    monkeypatch.setattr(cli, "configure_google_sources", configure)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "y"))

    # When: setup reaches the OAuth failure.
    exit_code = cli.main(["setup"])

    # Then: no service prompt or install happens after the failure.
    assert exit_code == 2
    assert session.events == [PROMPT, PROMPT, OAUTH_FAILURE]
    assert not session.unit_path.exists()


def test_setup_empty_service_answer_defaults_to_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the user accepts the default (empty) service-consent answer.
    session = _begin_session(monkeypatch, tmp_path)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", ""))

    # When: setup asks whether to register the watcher after OAuth.
    exit_code = cli.main(["setup"])

    # Then: empty input is yes and the shared install core runs once.
    assert exit_code == 0
    assert session.events == [PROMPT, PROMPT, OAUTH_SUCCESS, PROMPT, SERVICE_INSTALL]


def test_setup_reuses_execute_service_without_cli_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the shared service core exists for in-process reuse.
    assert callable(getattr(service_cli, "execute_service", None))
    session = _begin_session(monkeypatch, tmp_path)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "y"))

    def subprocess_run(
        command: Sequence[str], *_args: str, **_kwargs: str
    ) -> subprocess.CompletedProcess[str]:
        if "service" in command and "install" in command:
            session.events.append(SUBPROCESS_CLI)
        return subprocess.CompletedProcess(list(command), 0, "", "")

    monkeypatch.setattr(subprocess, "run", subprocess_run)

    # When: the user consents to watcher registration after OAuth.
    exit_code = cli.main(["setup"])

    # Then: setup calls execute_service("install") without a CLI subprocess.
    assert exit_code == 0
    assert SERVICE_INSTALL in session.events
    assert RUN_SERVICE not in session.events
    assert SUBPROCESS_CLI not in session.events


def test_setup_install_failure_preserves_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: service install will return a typed failure after OAuth success.
    session = _begin_session(monkeypatch, tmp_path, _failed_result())
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "y"))

    # When: setup attempts watcher registration.
    exit_code = cli.main(["setup"])

    # Then: opted-in install failure returns 2 and the OAuth canary is unchanged.
    assert exit_code == 2
    assert SERVICE_INSTALL in session.events
    assert session.oauth_canary.read_text(encoding="utf-8") == _OAUTH_CANARY


def test_setup_disabled_linger_prints_english_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: opted-in install succeeds while linger is disabled.
    session = _begin_session(
        monkeypatch, tmp_path, _installed_result(linger="disabled")
    )
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    _bind_tty(monkeypatch, session.events, _answers(session, "y", "y"))

    # When: setup registers the watcher after OAuth.
    _ = cli.main(["setup"])

    # Then: stdout has copy-paste linger English and the core stayed in-process.
    command = f"loginctl enable-linger {getpass.getuser()}"
    output = stdout.getvalue()
    assert command in output
    assert any(character.isalpha() for character in output.replace(command, ""))
    assert SERVICE_INSTALL in session.events


def test_setup_service_consent_keyboard_interrupt_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the service-consent prompt will raise KeyboardInterrupt.
    session = _begin_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "sys.stdin",
        _EventTTYStringIO(f"{session.client_path}\ny\n", session.events, 3),
    )

    # When: the user interrupts at the post-OAuth service question.
    with pytest.raises(KeyboardInterrupt):
        _ = cli.main(["setup"])

    # Then: OAuth already succeeded, no install ran, and the interrupt escaped.
    assert session.events == [PROMPT, PROMPT, OAUTH_SUCCESS, PROMPT]
    assert session.oauth_canary.read_text(encoding="utf-8") == _OAUTH_CANARY
