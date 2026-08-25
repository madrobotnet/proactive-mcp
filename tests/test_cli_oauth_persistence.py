import sqlite3
import sys
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from proactive_mcp import cli, sources
from proactive_mcp.store import (
    ReceiptErasurePendingError,
    Store,
    UnsafeDatabasePathError,
)
from tests.cli_oauth_test_support import (
    FIXTURES,
    FakeInstalledAppFlow,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)
from tests.cli_oauth_test_support import (
    install_fake_authorizer as _install_fake_authorizer,
)


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
    with Store(tmp_path / "state.db") as store:
        assert tuple(state.auth_state for state in store.list_source_sync()) == (
            "configured",
            "configured",
        )


@pytest.mark.parametrize(
    ("failure_phase", "storage_error"),
    [
        (
            "open",
            OSError("open-/private/path-canary provider-canary token-canary"),
        ),
        (
            "write",
            sqlite3.OperationalError(
                "write-/private/path-canary provider-canary token-canary"
            ),
        ),
        (
            "open",
            UnsafeDatabasePathError(
                Path("/private/path-canary"),
                "unsafe-provider-canary-token-canary",
            ),
        ),
        ("open", ReceiptErasurePendingError()),
    ],
)
def test_setup_entrypoint_redacts_post_authorization_storage_failures(
    failure_phase: str,
    storage_error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "state.db"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proactive-mcp",
            "setup",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ],
    )
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    class FailingStore:
        def __init__(self, _path: Path) -> None:
            if failure_phase == "open":
                raise storage_error

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc_value: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            pass

        def set_google_auth_state(self, _state: str) -> None:
            raise storage_error

    monkeypatch.setattr(sources, "Store", FailingStore)

    with pytest.raises(SystemExit) as raised:
        cli.entrypoint()
    captured = capsys.readouterr()

    assert raised.value.code == 2
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert captured.err == "error: Google setup could not be saved; run setup again\n"
    combined = captured.out + captured.err
    assert "Traceback" not in combined
    assert type(storage_error).__name__ not in combined
    assert str(tmp_path) not in combined
    assert "/private/" not in combined
    assert "provider-canary" not in combined
    assert "token-canary" not in combined


def test_setup_state_write_failure_rolls_back_both_auth_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "state.db"
    with Store(database) as store:
        store.set_google_auth_state("not_configured")
        _ = store.connection().executescript(
            """
            CREATE TRIGGER fail_calendar_setup
            BEFORE UPDATE ON source_sync_state
            WHEN NEW.source = 'calendar'
            BEGIN
                SELECT RAISE(ABORT, 'state-write-token-canary');
            END;
            """
        )
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    _install_fake_authorizer(monkeypatch, FakeInstalledAppFlow(google_credential()))

    result = cli.main(
        [
            "setup",
            "--headless",
            "--client-secrets",
            str(FIXTURES / "installed-client.json"),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert count_setup_success_events(captured.out, captured.err) == 0
    assert captured.err == "error: Google setup could not be saved; run setup again\n"
    assert "state-write-token-canary" not in captured.out + captured.err
    with Store(database) as store:
        assert tuple(state.auth_state for state in store.list_source_sync()) == (
            "not_configured",
            "not_configured",
        )
