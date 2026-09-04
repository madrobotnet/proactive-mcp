"""Hermetic CLI tests for legacy setup backward compatibility regressions."""

from __future__ import annotations

import io
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


class _NoPromptTTYStringIO(_DeterministicTTYStringIO):
    """Fail if a setup run attempts to read an interactive prompt response."""

    @override
    def readline(self, _size: int = -1) -> str:
        raise AssertionError(_NON_INTERACTIVE_PROMPT_ERROR)


class _NoPromptNonTTYStringIO(io.StringIO):
    """Fail if a non-TTY setup run attempts to read from stdin."""

    @override
    def readline(self, _size: int = -1) -> str:
        raise AssertionError(_NON_INTERACTIVE_PROMPT_ERROR)


@pytest.fixture
def fake_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[Path, GoogleSetupOptions]]:
    """Capture calls to configure_google_sources locally."""
    captured: list[tuple[Path, GoogleSetupOptions]] = []

    def configure(path: Path, options: GoogleSetupOptions) -> None:
        captured.append((path, options))

    monkeypatch.setattr(cli, "configure_google_sources", configure)
    return captured


@pytest.fixture
def non_tty_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_NoPromptNonTTYStringIO, io.StringIO]:
    """Deterministic typed non-TTY stream fixture."""
    stdin_stream = _NoPromptNonTTYStringIO()
    stdout_stream = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)
    return stdin_stream, stdout_stream


@pytest.fixture
def tty_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_NoPromptTTYStringIO, io.StringIO]:
    """Deterministic typed TTY stream fixture."""
    stdin_stream = _NoPromptTTYStringIO()
    stdout_stream = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_stream)
    monkeypatch.setattr("sys.stdout", stdout_stream)
    return stdin_stream, stdout_stream


def test_legacy_setup_flag_client_secrets_bypasses_wizard_on_non_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
    non_tty_streams: tuple[_NoPromptNonTTYStringIO, io.StringIO],
) -> None:
    """Assert --client-secrets bypasses wizard on deterministic non-TTY stdin."""
    database_path = tmp_path / "state" / "proactive.db"
    explicit_path = tmp_path / "legacy-client-secret.json"
    env_path = tmp_path / "env-client-secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(env_path))

    _, stdout_stream = non_tty_streams
    exit_code = cli.main(["setup", "--client-secrets", str(explicit_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert stdout_stream.getvalue() == ""
    assert captured.out == ""
    assert captured.err == ""
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == explicit_path
    assert options.headless is False
    assert options.reauth is False


def test_legacy_setup_flag_headless_bypasses_wizard_on_non_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
    non_tty_streams: tuple[_NoPromptNonTTYStringIO, io.StringIO],
) -> None:
    """Assert --headless bypasses wizard on deterministic non-TTY stdin."""
    database_path = tmp_path / "state" / "proactive.db"
    env_path = tmp_path / "env-client-secret.json"
    state_default_path = database_path.parent / "client_secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(env_path))

    _, stdout_stream = non_tty_streams
    exit_code = cli.main(["setup", "--headless"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert stdout_stream.getvalue() == ""
    assert captured.out == ""
    assert captured.err == ""
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == env_path
    assert options.client_secrets_path != state_default_path
    assert options.headless is True
    assert options.reauth is False


def test_legacy_setup_flag_reauth_bypasses_wizard_on_non_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
    non_tty_streams: tuple[_NoPromptNonTTYStringIO, io.StringIO],
) -> None:
    """Assert --reauth bypasses wizard on deterministic non-TTY stdin."""
    database_path = tmp_path / "state" / "proactive.db"
    state_default_path = database_path.parent / "client_secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.delenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", raising=False)

    _, stdout_stream = non_tty_streams
    exit_code = cli.main(["setup", "--reauth"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert stdout_stream.getvalue() == ""
    assert captured.out == ""
    assert captured.err == ""
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == state_default_path
    assert options.headless is False
    assert options.reauth is True


def test_legacy_setup_flags_headless_and_client_secrets_on_tty_bypasses_wizard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_configure: list[tuple[Path, GoogleSetupOptions]],
    tty_streams: tuple[_NoPromptTTYStringIO, io.StringIO],
) -> None:
    """Assert historical --headless --client-secrets <path> on TTY does not prompt."""
    database_path = tmp_path / "state" / "proactive.db"
    explicit_path = tmp_path / "historical-client-secret.json"
    env_path = tmp_path / "env-client-secret.json"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database_path))
    monkeypatch.setenv("PROACTIVE_GOOGLE_CLIENT_SECRETS", str(env_path))

    _, stdout_stream = tty_streams
    exit_code = cli.main(
        ["setup", "--headless", "--client-secrets", str(explicit_path)]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert stdout_stream.getvalue() == ""
    assert captured.out == ""
    assert captured.err == ""
    assert len(fake_configure) == 1
    target_path, options = fake_configure[0]
    assert target_path == database_path
    assert options.client_secrets_path == explicit_path
    assert options.headless is True
    assert options.reauth is False
