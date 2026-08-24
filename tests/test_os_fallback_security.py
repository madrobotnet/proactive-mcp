from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from proactive_mcp.delivery.notify import (
    DEFAULT_NOTIFICATION_TIMEOUT,
    MACOS_NOTIFICATION_SCRIPT,
    WINDOWS_TOAST_SCRIPT,
    NotificationError,
    NotificationHost,
    SubprocessNotificationRunner,
    parse_notification_platform,
    send_os_notification,
    trusted_notifier_path,
)
from proactive_mcp.delivery.payload import NotificationPayload, notification_payload
from proactive_mcp.store import Detection, SituationEvidence

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta

    from proactive_mcp.delivery.notify import NotificationPlatform
    from proactive_mcp.store import SituationType

CANARY_SUBJECT = "CANARY_SUBJECT_Q3-layoff-list"
CANARY_SENDER = "CANARY_SENDER_alice.secret@corp.example"
CANARY_EVENT_TITLE = "CANARY_EVENT_stealth-acquisition"
SERVER_TITLE = "Calendar conflict on 2026-08-22"
POISON_TITLE = "Alice Johnson"
INJECTION_TITLE = '-e "pwned"; -Command Start-Process calc; $(whoami)'
_NOTIFY = (
    trusted_notifier_path("linux"),
    "--",
    SERVER_TITLE,
    "calendar_conflict",
)


@dataclass(frozen=True, slots=True)
class _AllowedFields:
    situation_type: SituationType
    title: str

    def __getattr__(self, name: str) -> str:
        message = f"notification payload read {name}"
        raise AssertionError(message)


class _RecordingRunner:
    """Collect argv vectors; mutation is the test probe."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], timedelta]] = []

    def run(self, argv: Sequence[str], timeout: timedelta) -> None:
        self.calls.append((tuple(argv), timeout))


def _canary_detection() -> Detection:
    return Detection(
        situation_type="calendar_conflict",
        dedupe_key="calendar_conflict:a:b",
        priority="critical",
        title=SERVER_TITLE,
        why_now=f"overlap involving {CANARY_EVENT_TITLE}",
        evidence=SituationEvidence(
            facts={"event_a_id": "evt-a", "event_b_id": "evt-b"},
            quoted_external={
                "subject": CANARY_SUBJECT,
                "sender": CANARY_SENDER,
                "event_a": CANARY_EVENT_TITLE,
            },
        ),
    )


def _assert_no_canaries(text: str) -> None:
    assert CANARY_SUBJECT not in text
    assert CANARY_SENDER not in text
    assert CANARY_EVENT_TITLE not in text


def _send(
    payload: NotificationPayload,
    platform: NotificationPlatform,
) -> tuple[str, ...]:
    runner = _RecordingRunner()
    send_os_notification(payload, NotificationHost(platform, runner))
    assert len(runner.calls) == 1
    argv, timeout = runner.calls[0]
    assert timeout == DEFAULT_NOTIFICATION_TIMEOUT
    return argv


def test_payload_reads_only_situation_type_and_uses_fixed_label() -> None:
    source = _AllowedFields("calendar_conflict", POISON_TITLE)

    payload = notification_payload(source)

    assert payload == NotificationPayload("calendar_conflict", "Calendar conflict")
    assert tuple(NotificationPayload.__dataclass_fields__) == (
        "situation_type",
        "title",
    )


def test_payload_drops_quoted_external_and_why_now() -> None:
    payload = notification_payload(_canary_detection())

    assert payload.situation_type == "calendar_conflict"
    assert payload.title == "Calendar conflict"
    assert POISON_TITLE not in repr(payload)
    _assert_no_canaries(payload.situation_type)
    _assert_no_canaries(payload.title)
    _assert_no_canaries(repr(payload))


@pytest.mark.parametrize(
    ("platform", "prefix"),
    [
        ("linux", (trusted_notifier_path("linux"), "--")),
        (
            "darwin",
            (trusted_notifier_path("darwin"), str(MACOS_NOTIFICATION_SCRIPT)),
        ),
        (
            "win32",
            (
                trusted_notifier_path("win32"),
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(WINDOWS_TOAST_SCRIPT),
            ),
        ),
    ],
)
def test_os_argv_is_title_and_body_after_fixed_prefix(
    platform: NotificationPlatform,
    prefix: tuple[str, ...],
) -> None:
    argv = _send(
        NotificationPayload("calendar_conflict", "Calendar conflict"), platform
    )

    assert argv == (*prefix, "Calendar conflict", "calendar_conflict")
    assert "-e" not in prefix
    assert "-Command" not in prefix
    assert "-EncodedCommand" not in prefix


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_quoted_external_canaries_cannot_cross_notification_boundary(
    platform: NotificationPlatform,
) -> None:
    payload = notification_payload(_canary_detection())
    argv = _send(payload, platform)
    macos = MACOS_NOTIFICATION_SCRIPT.read_text(encoding="utf-8")
    windows = WINDOWS_TOAST_SCRIPT.read_text(encoding="utf-8")

    _assert_no_canaries(payload.title)
    _assert_no_canaries(payload.situation_type)
    _assert_no_canaries("\0".join(argv))
    _assert_no_canaries(macos)
    _assert_no_canaries(windows)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_injection_title_stays_one_argv_element(
    platform: NotificationPlatform,
) -> None:
    argv = _send(NotificationPayload("calendar_conflict", INJECTION_TITLE), platform)

    assert argv[-2] == INJECTION_TITLE
    assert argv[-1] == "calendar_conflict"
    assert argv.count(INJECTION_TITLE) == 1


def test_subprocess_runner_uses_argv_without_shell_or_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[str, ...], Mapping[str, object]]] = []

    def fake_run(
        argv: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        seen.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    SubprocessNotificationRunner().run(_NOTIFY, DEFAULT_NOTIFICATION_TIMEOUT)

    assert len(seen) == 1
    argv, options = seen[0]
    assert argv == _NOTIFY
    assert options["check"] is False
    assert options["shell"] is False
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL
    assert options["timeout"] == DEFAULT_NOTIFICATION_TIMEOUT.total_seconds()
    assert options["cwd"] == str(Path(_NOTIFY[0]).parent)
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"] != "attacker"


def test_hostile_path_never_controls_notifier_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "attacker")

    argv = _send(NotificationPayload("calendar_conflict", "Calendar conflict"), "linux")

    assert argv[0] == "/usr/bin/notify-send"
    assert "attacker" not in argv[0]


def test_timeout_error_is_redacted_code_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(
        argv: Sequence[str],
        **_kwargs: bool | float,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            cmd=argv,
            timeout=5,
            output=CANARY_SUBJECT.encode(),
            stderr=CANARY_SENDER.encode(),
        )

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(NotificationError) as captured:
        SubprocessNotificationRunner().run(_NOTIFY, DEFAULT_NOTIFICATION_TIMEOUT)

    assert captured.value.error_code == "timeout"
    assert str(captured.value) == "timeout"
    assert captured.value.__cause__ is None
    _assert_no_canaries(str(captured.value))
    _assert_no_canaries(repr(captured.value))


def test_missing_binary_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(
        _argv: Sequence[str],
        **_kwargs: bool | float,
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "missing", "notify-send")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(NotificationError) as captured:
        SubprocessNotificationRunner().run(_NOTIFY, DEFAULT_NOTIFICATION_TIMEOUT)

    assert captured.value.error_code == "unavailable"
    assert str(captured.value) == "unavailable"
    assert captured.value.__cause__ is None


def test_nonzero_exit_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(
        argv: Sequence[str],
        **_kwargs: bool | float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(NotificationError) as captured:
        SubprocessNotificationRunner().run(_NOTIFY, DEFAULT_NOTIFICATION_TIMEOUT)

    assert captured.value.error_code == "failed"
    assert str(captured.value) == "failed"
    assert captured.value.__cause__ is None


def test_unknown_platform_is_unsupported() -> None:
    with pytest.raises(NotificationError) as captured:
        _ = parse_notification_platform("freebsd")

    assert captured.value.error_code == "unsupported_platform"
    assert str(captured.value) == "unsupported_platform"


def test_packaged_scripts_are_argv_templates() -> None:
    macos = MACOS_NOTIFICATION_SCRIPT.read_bytes()
    windows = WINDOWS_TOAST_SCRIPT.read_bytes()

    assert b"on run argv" in macos
    assert b"display notification" in macos
    assert b"do shell script" not in macos
    assert b" -e " not in macos
    assert b"ToastNotificationManager" in windows
    assert b"CreateTextNode" in windows
    assert b"$ErrorActionPreference = 'Stop'" in windows
    assert b"try {" in windows
    assert b"catch {" in windows
    assert (
        b"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
        b"\\WindowsPowerShell\\v1.0\\powershell.exe"
    ) in windows
    assert b"CreateToastNotifier($aumid)" in windows
    assert b"CreateToastNotifier()" not in windows
    assert b"exit 0" in windows
    assert b"exit 1" in windows
    assert b"-Command" not in windows
    assert b"Invoke-Expression" not in windows
    assert b"Out-File" not in windows
    assert b"Set-Content" not in windows
    _assert_no_canaries(macos.decode("utf-8"))
    _assert_no_canaries(windows.decode("utf-8"))
