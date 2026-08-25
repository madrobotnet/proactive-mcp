"""Daemon source sync, evaluation, and notification failures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, NoReturn

from proactive_mcp import cli
from proactive_mcp.delivery.evaluation import EvaluationService
from proactive_mcp.delivery.fallback import FallbackDispatcher
from proactive_mcp.sources.lazy_sync import ScheduledSourceProvider
from tests.daemon_cli_test_support import UntrustedPhaseError as _UntrustedPhaseError

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    import pytest


def test_source_sync_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: source preparation raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_source(_provider: ScheduledSourceProvider) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(ScheduledSourceProvider, "prepare_sources", fail_source)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: source failure identity is bounded and safe.
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "source_sync", "code": "failed"}
    assert "canary" not in captured.err


def test_evaluation_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: evaluation raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_evaluation(_service: EvaluationService) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(EvaluationService, "run_once", fail_evaluation)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: evaluation failure identity is bounded and safe.
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "evaluation", "code": "failed"}
    assert "canary" not in captured.err


def test_notification_failure_emits_only_phase_and_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: notification dispatch raises untrusted exception text.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))

    def fail_notification(_dispatcher: FallbackDispatcher, _now: datetime) -> NoReturn:
        raise _UntrustedPhaseError

    monkeypatch.setattr(FallbackDispatcher, "dispatch", fail_notification)

    # When: the daemon runs one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()

    # Then: notification failure identity is bounded and safe.
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"phase": "notification", "code": "failed"}
    assert "canary" not in captured.err
