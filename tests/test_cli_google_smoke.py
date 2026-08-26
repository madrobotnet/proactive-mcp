import os
from dataclasses import replace
from pathlib import Path

import pytest

from proactive_mcp import cli
from proactive_mcp.sources import (
    GoogleReadDependencies,
    GoogleReadSummary,
    GoogleSyncService,
)
from proactive_mcp.store import Store
from tests.cli_behavior_test_support import (
    CurrentSmokeResponse as _CurrentSmokeResponse,
)
from tests.cli_behavior_test_support import (
    LegacySmokeResponse as _LegacySmokeResponse,
)
from tests.cli_behavior_test_support import (
    run_cli,
)
from tests.cli_sync_test_support import (
    FakeCalendarReader,
    FakeCredentials,
    FakeInboxReader,
    calendar_result,
    gmail_inbox_result,
)


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
