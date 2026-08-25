"""Daemon one-shot output and persisted diagnostics behavior."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from proactive_mcp import cli
from proactive_mcp.cli import daemon as daemon_cli
from proactive_mcp.cli.daemon import DaemonOnceResponse
from proactive_mcp.delivery.daemon import DaemonPass, WatcherDaemon
from proactive_mcp.delivery.evaluation import EvaluationPass, SkippedSources
from proactive_mcp.situations.engine import EvaluationResult
from proactive_mcp.store import SourceFreshness, Store
from proactive_mcp.store.sync import SourceReadDiagnostics, SourceReadReasonCount
from tests.daemon_cli_test_support import (
    CONFIG_MINUTES as _CONFIG_MINUTES,
)
from tests.daemon_cli_test_support import (
    START as _START,
)
from tests.daemon_cli_test_support import (
    ok_daemon as _ok_daemon,
)
from tests.daemon_cli_test_support import (
    once_payload as _once_payload,
)
from tests.daemon_cli_test_support import (
    run_cli as _run_cli,
)
from tests.situation_test_support import FakeClock

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.clock import Clock


def test_once_exits_zero_on_a_degraded_pass_without_google_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an isolated database and no Google credentials.
    database = tmp_path / "proactive.db"
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    # When: the daemon runs exactly one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()
    payload = DaemonOnceResponse.model_validate_json(captured.out)
    with Store(database, clock=FakeClock(_START)) as store:
        status = store.daemon.status()

    # Then: a degraded local-only pass is success, not an infrastructure failure.
    assert result == 0
    assert captured.err == ""
    assert payload.sources == "not_configured"
    assert payload.gmail == "not_configured"
    assert payload.calendar == "not_configured"
    assert payload.warning_count > 0
    assert status.liveness == "stopped"
    assert status.cycle_count == 1
    assert status.poll_interval == timedelta(minutes=_CONFIG_MINUTES)


def test_once_reuses_latest_persisted_diagnostics_after_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "proactive.db"
    diagnostics = SourceReadDiagnostics(
        outcome="partial",
        request_count=19,
        page_count=4,
        projected_count=6,
        excluded_count=13,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount("pagination_limit", 3),),
    )
    with Store(database, clock=FakeClock(_START)) as store:
        store.record_gmail_sync(diagnostics, error_code="degraded")
    monkeypatch.setenv("PROACTIVE_DATABASE", str(database))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    assert cli.main(["daemon", "--once"]) == 0
    payload = DaemonOnceResponse.model_validate_json(capsys.readouterr().out)

    assert payload.gmail_diagnostics.model_dump() == {
        "outcome": "partial",
        "request_count": 19,
        "page_count": 4,
        "projected_count": 6,
        "excluded_count": 13,
        "byte_budget": 8_000_000,
        "reason_counts": {"pagination_limit": 3},
    }


def test_once_exits_zero_on_an_ok_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a watcher whose one pass reports healthy sources.
    monkeypatch.setenv("PROACTIVE_DATABASE", str(tmp_path / "proactive.db"))
    monkeypatch.setattr(daemon_cli, "daemon_clock", lambda: FakeClock(_START))

    def open_ok(_store: Store, _clock: Clock) -> WatcherDaemon:
        return _ok_daemon()

    monkeypatch.setattr(daemon_cli, "open_watcher_daemon", open_ok)

    # When: the daemon runs exactly one pass.
    result = cli.main(["daemon", "--once"])
    captured = capsys.readouterr()
    payload = DaemonOnceResponse.model_validate_json(captured.out)

    # Then: a healthy completed pass is also success.
    assert result == 0
    assert payload.sources == "prepared"
    assert payload.gmail == "ok"
    assert payload.gmail_diagnostics.outcome == "healthy"
    assert payload.gmail_diagnostics.request_count == 0
    assert payload.calendar == "ok"
    assert payload.warning_count == 0


def test_once_skip_uses_freshness_derived_zero_counter_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a pass skips a read because persisted source freshness is current.
    freshness = SourceFreshness("ok", _START, _START, 0, None)
    result = EvaluationResult(0, 0, 0, 0, 0, 0, (), freshness, freshness)
    completed = DaemonPass(
        EvaluationPass(result, SkippedSources("already_fresh"), ()),
        (),
    )

    # When: the existing once output serializes the skipped pass.
    payload = _once_payload(completed, tmp_path, monkeypatch, capsys)

    # Then: skip output derives a healthy, zero-counter compatibility value.
    assert payload.sources == "already_fresh"
    assert payload.gmail_diagnostics.outcome == "healthy"
    assert payload.gmail_diagnostics.request_count == 0
    assert payload.gmail_diagnostics.reason_counts == {}


@pytest.mark.parametrize("reason", ["already_fresh", "sync_in_flight"])
def test_once_skip_keeps_latest_accepted_nonzero_diagnostics(
    reason: Literal["already_fresh", "sync_in_flight"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a prior accepted read has nonzero counters before a skipped pass.
    freshness = SourceFreshness("ok", _START, _START, 0, None)
    diagnostics = SourceReadDiagnostics(
        outcome="partial",
        request_count=12,
        page_count=3,
        projected_count=4,
        excluded_count=8,
        byte_budget=8_000_000,
        reason_counts=(SourceReadReasonCount("pagination_limit", 2),),
    )
    result = EvaluationResult(
        0,
        0,
        0,
        0,
        0,
        0,
        (),
        freshness,
        freshness,
        accepted_gmail_diagnostics=diagnostics,
    )
    completed = DaemonPass(EvaluationPass(result, SkippedSources(reason), ()), ())

    # When: daemon output reports an already-fresh or coalesced check.
    payload = _once_payload(completed, tmp_path, monkeypatch, capsys)

    # Then: skip never replaces accepted counters with freshness-derived zeros.
    assert payload.sources == reason
    assert payload.gmail_diagnostics.request_count == 12
    assert payload.gmail_diagnostics.reason_counts == {"pagination_limit": 2}


def test_once_subprocess_exits_without_hanging_and_stays_pii_free(
    tmp_path: Path,
) -> None:
    # Given: an isolated database and no Google credentials.
    env = os.environ | {"PROACTIVE_DATABASE": str(tmp_path / "proactive.db")}

    # When: a real process runs the once-path.
    result = _run_cli("daemon", "--once", env=env)

    # Then: it exits successfully with only structural, non-secret output.
    assert result.returncode == 0
    combined = f"{result.stdout}{result.stderr}"
    assert "Traceback" not in combined
    assert "@" not in combined
    payload = DaemonOnceResponse.model_validate_json(result.stdout)
    assert payload.sources == "not_configured"
    assert payload.gmail_diagnostics.outcome == "stale"
    assert "path" not in payload.gmail_diagnostics.model_dump()
