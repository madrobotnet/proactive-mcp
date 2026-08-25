import json
from pathlib import Path

import pytest

from proactive_mcp import cli
from proactive_mcp.store import ReceiptErasurePendingError


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


def test_shared_cli_boundary_redacts_pending_erasure_for_disconnect(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def pending_erasure(_path: Path) -> None:
        raise ReceiptErasurePendingError

    monkeypatch.setattr(cli, "disconnect_google_sources", pending_erasure)

    result = cli.main(["disconnect"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert (
        captured.err
        == "error: receipt erasure is blocked; close older processes and retry\n"
    )
    assert "Traceback" not in captured.err
    assert "ReceiptErasurePendingError" not in captured.err


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
