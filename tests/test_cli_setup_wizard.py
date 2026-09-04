"""Hermetic CLI tests for issue #42 interactive setup wizard."""

from __future__ import annotations

import io
import sys
from typing import TYPE_CHECKING, Final

import pytest
from typing_extensions import override

from proactive_mcp import cli

_NON_INTERACTIVE_PROMPT_ERROR: Final = "non-interactive setup must not prompt for input"

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.sources import GoogleSetupOptions


class _DeterministicTTYStringIO(io.StringIO):
    """Hermetic deterministic in-memory stream presenting as a TTY."""

    @override
    def isatty(self) -> bool:
        return True


class _PromptOrderTTYStringIO(_DeterministicTTYStringIO):
    """Record how much prompt output exists before each input read."""

    def __init__(self, value: str, prompt_stream: io.StringIO) -> None:
        super().__init__(value)
        self._prompt_stream: io.StringIO = prompt_stream
        self.prompt_lengths: list[int] = []

    @override
    def readline(self, size: int = -1) -> str:
        self.prompt_lengths.append(len(self._prompt_stream.getvalue()))
        return super().readline(size)


class _NoPromptTTYStringIO(_DeterministicTTYStringIO):
    """Fail if a non-interactive setup path attempts to read a prompt response."""

    @override
    def readline(self, _size: int = -1) -> str:
        raise AssertionError(_NON_INTERACTIVE_PROMPT_ERROR)


@pytest.fixture
def fake_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[Path, GoogleSetupOptions]]:
    """Capture calls to configure_google_sources."""
    captured: list[tuple[Path, GoogleSetupOptions]] = []

    def configure(path: Path, options: GoogleSetupOptions) -> None:
        captured.append((path, options))

    monkeypatch.setattr(cli, "configure_google_sources", configure)
    return captured


def test_interactive_setup_prompts_oauth_path_then_browser_and_wires_browser_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Assert OAuth path prompt then browser question, wiring open browser."""
    database_path = tmp_path / "state" / "proactive.db"
    entered_client_path = tmp_path / "custom-client-secret.json"
    _ = entered_client_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setattr(sys, "platform", "linux")

    # User enters custom path, answers 'y' (open browser), then declines service.
    stdout_stream = io.StringIO()
    stdin_stream = _PromptOrderTTYStringIO(
        f"{entered_client_path}\ny\nn\n",
        stdout_stream,
    )

    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert len(stdin_stream.prompt_lengths) == 3
    first_prompt_length, second_prompt_length = stdin_stream.prompt_lengths[:2]
    assert first_prompt_length > 0
    assert second_prompt_length > first_prompt_length

    # Answering 'y' to open browser means headless must be False.
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == entered_client_path
    assert options.headless is False


def test_interactive_setup_prompts_oauth_path_then_browser_and_wires_browser_no(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Assert OAuth path prompt then browser question, wiring headless inversion."""
    database_path = tmp_path / "state" / "proactive.db"
    entered_client_path = tmp_path / "another-client-secret.json"
    _ = entered_client_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setattr(sys, "platform", "linux")

    # User enters custom path, answers 'n' (headless), then declines service.
    stdout_stream = io.StringIO()
    stdin_stream = _PromptOrderTTYStringIO(
        f"{entered_client_path}\nn\nn\n",
        stdout_stream,
    )

    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)

    exit_code = cli.main(["setup"])

    assert exit_code == 0
    assert len(stdin_stream.prompt_lengths) == 3
    first_prompt_length, second_prompt_length = stdin_stream.prompt_lengths[:2]
    assert first_prompt_length > 0
    assert second_prompt_length > first_prompt_length

    # Answering 'n' to open browser means headless must be True (headless inversion).
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == entered_client_path
    assert options.headless is True


def test_non_interactive_setup_skips_prompts_and_prefers_explicit_client_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Preserve legacy explicit/headless automation without wizard prompts."""
    database_path = tmp_path / "state" / "proactive.db"
    explicit_path = tmp_path / "explicit-client-secret.json"
    environment_path = tmp_path / "environment-client-secret.json"
    state_default_path = database_path.parent / "client_secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(environment_path))
    stdin_stream = _NoPromptTTYStringIO()
    stdout_stream = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)

    exit_code = cli.main(
        [
            "setup",
            "--non-interactive",
            "--headless",
            "--client-secrets",
            str(explicit_path),
        ]
    )

    assert exit_code == 0
    assert stdout_stream.getvalue() == ""
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == explicit_path
    assert options.client_secrets_path != environment_path
    assert options.client_secrets_path != state_default_path
    assert options.headless is True


def test_non_interactive_setup_uses_environment_client_secrets_when_explicit_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Use the environment client secret before the state-directory default."""
    database_path = tmp_path / "state" / "proactive.db"
    environment_path = tmp_path / "environment-client-secret.json"
    state_default_path = database_path.parent / "client_secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(environment_path))

    exit_code = cli.main(["setup", "--non-interactive", "--headless"])

    assert exit_code == 0
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == environment_path
    assert options.client_secrets_path != state_default_path
    assert options.headless is True


def test_non_interactive_setup_uses_state_default_when_higher_precedence_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Use the state-directory client secret when no override is configured."""
    database_path = tmp_path / "state" / "proactive.db"
    state_default_path = database_path.parent / "client_secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.delenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", raising=False)

    exit_code = cli.main(["setup", "--non-interactive", "--headless"])

    assert exit_code == 0
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == state_default_path
    assert options.headless is True


def test_interactive_setup_aborts_on_non_tty_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Abort when stdin is not a TTY without invoking OAuth or leaking inputs."""
    canary_path = tmp_path / "super-secret-canary-path.json"
    _ = canary_path.write_text("{}", encoding="utf-8")
    canary_payload = "super-secret-canary-payload"

    # Standard io.StringIO is not a TTY (isatty() -> False)
    stdin_stream = io.StringIO(f"{canary_path}\n{canary_payload}\n")
    stdout_stream = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)

    exit_code = cli.main(["setup"])
    captured = capsys.readouterr()
    combined_err = captured.err
    combined_out = stdout_stream.getvalue() + captured.out

    assert exit_code == 2
    assert len(fake_configure) == 0
    assert "--non-interactive" in combined_err
    assert "Traceback" not in combined_err
    assert "Traceback" not in combined_out
    assert str(canary_path) not in combined_err
    assert str(canary_path) not in combined_out
    assert canary_payload not in combined_err
    assert canary_payload not in combined_out


def test_interactive_setup_aborts_on_tty_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
) -> None:
    """Abort on EOF in TTY input without invoking OAuth or leaking secrets."""
    canary_path = tmp_path / "super-secret-eof-canary.json"
    _ = canary_path.write_text("{}", encoding="utf-8")

    # TTY stream that is empty (EOF immediately)
    stdin_stream = _DeterministicTTYStringIO("")
    stdout_stream = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)

    exit_code = cli.main(["setup"])
    captured = capsys.readouterr()
    combined_err = captured.err
    combined_out = stdout_stream.getvalue() + captured.out

    assert exit_code == 2
    assert len(fake_configure) == 0
    assert "--non-interactive" in combined_err
    assert "Traceback" not in combined_err
    assert "Traceback" not in combined_out
    assert str(canary_path) not in combined_err
    assert str(canary_path) not in combined_out


def test_interactive_setup_does_not_swallow_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert KeyboardInterrupt raised during interactive input propagates uncaught."""

    class _InterruptingStdin(_DeterministicTTYStringIO):
        @override
        def readline(self, _size: int = -1) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr("sys.stdin", _InterruptingStdin())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    with pytest.raises(KeyboardInterrupt):
        _ = cli.main(["setup"])


def test_interactive_setup_does_not_swallow_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert SystemExit raised during interactive input propagates uncaught."""

    class _ExitingStdin(_DeterministicTTYStringIO):
        @override
        def readline(self, _size: int = -1) -> str:
            raise SystemExit(1)

    monkeypatch.setattr("sys.stdin", _ExitingStdin())
    monkeypatch.setattr("sys.stdout", io.StringIO())

    with pytest.raises(SystemExit) as exc_info:
        _ = cli.main(["setup"])

    assert exc_info.value.code == 1
