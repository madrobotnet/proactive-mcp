import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

from proactive_mcp import cli
from proactive_mcp.store import UnsafeDatabasePathError
from tests.v10_migration_support import create_v9_database, insert_v9_claim

_SAFE_ERROR = (
    "error: database path is unsafe; choose a private user-owned directory and retry\n"
)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory modes")
def test_status_rejects_writable_ancestor_without_disclosing_diagnostics(
    tmp_path: Path,
) -> None:
    # Given: a real database path below an ancestor writable by other users.
    unsafe_ancestor = tmp_path / "path-canary"
    unsafe_ancestor.mkdir(mode=0o777)
    unsafe_ancestor.chmod(0o777)
    private_child = unsafe_ancestor / "private"
    private_child.mkdir(mode=0o700)
    database = private_child / "database-canary.db"

    # When: the real CLI opens its status store.
    result = subprocess.run(
        [sys.executable, "-m", "proactive_mcp", "status"],
        capture_output=True,
        text=True,
        env=os.environ | {"PROACTIVE_DATABASE": str(database)},
        check=False,
        timeout=15,
    )

    # Then: the process emits only the fixed application-owned remediation.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == _SAFE_ERROR
    assert "Traceback" not in result.stderr
    assert str(database) not in result.stderr
    assert "UnsafeDatabasePathError" not in result.stderr
    assert "ancestor directory is writable by others" not in result.stderr
    assert "path-canary" not in result.stderr
    assert "database-canary" not in result.stderr


def test_status_unsafe_path_message_is_independent_of_error_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the status store rejects attacker-controlled path and reason fields.
    path_canary = Path("/private/PR29_PATH_CANARY")
    reason_canary = "PR29_REASON_CANARY"
    storage_error = UnsafeDatabasePathError(path=path_canary, reason=reason_canary)
    assert storage_error.path == path_canary
    assert storage_error.reason == reason_canary

    def reject_status() -> NoReturn:
        raise storage_error

    monkeypatch.setattr(cli, "build_status", reject_status)

    # When: the shared non-daemon boundary handles the typed storage error.
    result = cli.main(["status"])
    captured = capsys.readouterr()

    # Then: neither typed field influences the fixed operator message.
    assert result == 2
    assert captured.out == ""
    assert captured.err == _SAFE_ERROR
    assert str(path_canary) not in captured.err
    assert reason_canary not in captured.err


@pytest.mark.parametrize(
    "programmer_error",
    [RuntimeError("runtime-canary"), KeyboardInterrupt(), SystemExit(19)],
)
def test_status_does_not_catch_programmer_or_process_control_errors(
    programmer_error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: status raises an error outside the explicit application contract.
    def fail_status() -> NoReturn:
        raise programmer_error

    monkeypatch.setattr(cli, "build_status", fail_status)

    # When/Then: the shared boundary preserves traceback and process semantics.
    with pytest.raises(type(programmer_error)) as raised:
        _ = cli.main(["status"])
    assert raised.value is programmer_error


def test_status_redacts_pending_receipt_erasure_then_retries_after_reader_closes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-token-canary.db"
    receipt_canary = "PR29_CLI_RECEIPT_TOKEN_CANARY_g3Q8vN5xK2mR7wL4"
    _ = create_v9_database(path)
    insert_v9_claim(path, receipt_canary)
    fixture_connection = sqlite3.connect(path)
    try:
        _ = fixture_connection.execute(
            """
            UPDATE source_sync_state SET auth_state = 'configured'
            WHERE source = 'gmail'
            """
        )
        fixture_connection.commit()
    finally:
        fixture_connection.close()
    env = os.environ | {"PROACTIVE_DATABASE": str(path)}
    command = [sys.executable, "-m", "proactive_mcp", "status"]

    legacy_reader = sqlite3.connect(path)
    try:
        legacy_reader.execute("PRAGMA journal_mode = WAL").close()
        legacy_reader.execute("BEGIN").close()
        assert legacy_reader.execute(
            "SELECT claim_token FROM situation_delivery_claims"
        ).fetchone() == (receipt_canary,)
        blocked = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=15,
        )
        assert blocked.returncode == 2
        assert blocked.stdout == ""
        assert (
            blocked.stderr
            == "error: receipt erasure is blocked; close older processes and retry\n"
        )
        assert "Traceback" not in blocked.stderr
        assert str(path) not in blocked.stderr
        assert receipt_canary not in blocked.stderr
        assert "ReceiptErasurePendingError" not in blocked.stderr
    finally:
        legacy_reader.rollback()
        legacy_reader.close()

    retried = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )
    assert retried.returncode == 0
    assert retried.stderr == ""
    assert json.loads(retried.stdout)["database"]["status"] == "healthy"
