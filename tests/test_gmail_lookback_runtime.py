from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from proactive_mcp import cli, sources
from proactive_mcp.paths import ProactivePaths
from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES, CredentialStore
from proactive_mcp.sources.gmail import GMAIL_THREADS_URL
from proactive_mcp.sources.lazy_sync import open_source_access
from proactive_mcp.sources.transport import GoogleHttpResponse
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pytest

    from proactive_mcp.clock import Clock
    from proactive_mcp.situations.inputs import InboxThreadSnapshot, SourceSnapshot
    from proactive_mcp.sources.credentials import GoogleCredential

_NOW: Final = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
_LOOKBACK: Final = timedelta(days=3)
_EXPECTED_QUERY: Final = f"after:{int((_NOW - _LOOKBACK).timestamp())}"


@dataclass(frozen=True, slots=True)
class _FixedClock:
    def now(self) -> datetime:
        return _NOW


@dataclass(frozen=True, slots=True)
class _FakeCredential:
    @property
    def refresh_token(self) -> str:
        return "redacted"

    @property
    def scopes(self) -> tuple[str, str]:
        return GOOGLE_READONLY_SCOPES

    def to_json(self) -> str:
        return "{}"


@dataclass(frozen=True, slots=True)
class _RecordingGoogleTransport:
    thread_count: int = 0
    page_size: int = 100
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def get(
        self,
        url: str,
        query: Mapping[str, str],
        *,
        max_bytes: int,
    ) -> GoogleHttpResponse:
        del max_bytes
        self.calls.append((url, dict(query)))
        if url.endswith("/profile"):
            body = (
                b'{"emailAddress":"owner@example.test","messagesTotal":0,'
                b'"threadsTotal":0,"historyId":"1"}'
            )
        elif url == GMAIL_THREADS_URL:
            offset = int(query.get("pageToken", "0"))
            end = min(offset + self.page_size, self.thread_count)
            payload: dict[str, list[dict[str, str]] | str] = {
                "threads": [{"id": f"thread-{index}"} for index in range(offset, end)]
            }
            if end < self.thread_count:
                payload["nextPageToken"] = str(end)
            body = json.dumps(payload).encode()
        elif url.startswith(f"{GMAIL_THREADS_URL}/"):
            body = (
                b'{"messages":[{"id":"message","labelIds":["INBOX"],'
                b'"internalDate":"1787302800000","payload":{"mimeType":'
                b'"text/plain","body":{"data":"Ym91bmRlZA"}}}]}'
            )
        else:
            body = b'{"items":[]}'
        return GoogleHttpResponse(status_code=200, body=body)


@dataclass(frozen=True, slots=True)
class _TransportFactory:
    transport: _RecordingGoogleTransport

    def __call__(self, _credential: GoogleCredential) -> _RecordingGoogleTransport:
        return self.transport


@dataclass(frozen=True, slots=True)
class _CapacityScenario:
    thread_count: int
    page_size: int


def _configured_paths(root: Path) -> ProactivePaths:
    paths = ProactivePaths.for_database(root / "state" / "proactive.db")
    paths.state_directory.mkdir()
    _ = paths.config.write_text(
        "[sources]\ngmail_lookback_days = 3\n",
        encoding="utf-8",
    )
    return paths


def _gmail_query(transport: _RecordingGoogleTransport) -> dict[str, str]:
    return next(query for url, query in transport.calls if url == GMAIL_THREADS_URL)


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: _RecordingGoogleTransport,
) -> None:
    monkeypatch.setattr(
        sources,
        "GoogleAuthenticatedGetTransport",
        _TransportFactory(transport),
    )


def _read_gmail_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: _CapacityScenario,
) -> tuple[SourceSnapshot[InboxThreadSnapshot], _RecordingGoogleTransport]:
    transport = _RecordingGoogleTransport(
        thread_count=scenario.thread_count,
        page_size=scenario.page_size,
    )
    _patch_transport(monkeypatch, transport)
    clock: Clock = _FixedClock()
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        service = sources.GoogleReadServiceFactory(
            store=store,
            clock=clock,
            credentials=CredentialStore(tmp_path),
            gmail_lookback=_LOOKBACK,
        ).open(_FakeCredential())
        snapshot = service.prepare_evaluation().gmail_threads
    assert snapshot is not None
    return snapshot, transport


def test_factory_reads_more_than_64_threads_with_configured_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 97 compact in-range threads returned through the production factory.
    scenario = _CapacityScenario(thread_count=97, page_size=97)

    # When: the composed Gmail adapter projects every listed thread.
    snapshot, transport = _read_gmail_capacity(tmp_path, monkeypatch, scenario)

    # Then: the old 64-request ceiling does not truncate the healthy read.
    gmail_calls = [
        call for call in transport.calls if "gmail.googleapis.com" in call[0]
    ]
    assert len(snapshot.items) == 97
    assert len(gmail_calls) == 99
    assert snapshot.complete is True
    assert "sync_budget_exhausted" not in snapshot.warning_codes


def test_factory_capacity_reaches_200_threads_at_request_budget_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: exactly 200 threads spread over all 20 permitted list pages.
    scenario = _CapacityScenario(thread_count=200, page_size=10)

    # When: the factory-created adapter performs the bounded read.
    snapshot, transport = _read_gmail_capacity(tmp_path, monkeypatch, scenario)

    # Then: profile + pages + details exactly consumes the 221-request budget.
    gmail_calls = [
        call for call in transport.calls if "gmail.googleapis.com" in call[0]
    ]
    assert len(snapshot.items) == 200
    assert len(gmail_calls) == 221
    assert snapshot.complete is True


def test_factory_marks_201st_listed_thread_as_explicitly_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 201 listed in-range threads fit within the page boundary.
    scenario = _CapacityScenario(thread_count=201, page_size=11)

    # When: the factory-created adapter applies the 200-thread projection cap.
    snapshot, transport = _read_gmail_capacity(tmp_path, monkeypatch, scenario)

    # Then: one exclusion and its reason are explicit instead of false healthy output.
    gmail_calls = [
        call for call in transport.calls if "gmail.googleapis.com" in call[0]
    ]
    assert len(snapshot.items) == 200
    assert len(gmail_calls) == 220
    assert snapshot.complete is False
    assert len(snapshot.resolution_excluded_ids) == 1
    assert "thread_projection_limit" in snapshot.warning_codes


def test_google_smoke_uses_configured_lookback_in_generated_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: smoke state with a non-default lookback and stored credentials.
    paths = _configured_paths(tmp_path)
    transport = _RecordingGoogleTransport()

    def load_credentials(_store: CredentialStore) -> GoogleCredential:
        return _FakeCredential()

    monkeypatch.setattr(CredentialStore, "load", load_credentials)
    _patch_transport(monkeypatch, transport)
    monkeypatch.setattr(sources, "UtcClock", _FixedClock)

    # When: the production smoke composition performs its confirmed read.
    _ = sources.run_google_read_smoke(paths.database, enabled=True)

    # Then: Gmail receives the configured bounded INBOX query.
    query = _gmail_query(transport)
    assert query["q"] == _EXPECTED_QUERY
    assert query["labelIds"] == "INBOX"


def test_google_read_service_factory_forwards_supplied_lookback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a factory composed with a non-default lookback.
    transport = _RecordingGoogleTransport()
    _patch_transport(monkeypatch, transport)
    clock: Clock = _FixedClock()
    with Store(tmp_path / "proactive.db", clock=clock) as store:
        factory = sources.GoogleReadServiceFactory(
            store=store,
            clock=clock,
            credentials=CredentialStore(tmp_path),
            gmail_lookback=_LOOKBACK,
        )

        # When: its credential-backed reader prepares source snapshots.
        _ = factory.open(_FakeCredential()).prepare_evaluation()

    # Then: the factory-created Gmail adapter receives that lookback.
    assert _gmail_query(transport)["q"] == _EXPECTED_QUERY


def test_open_source_access_loads_configured_lookback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: source access beside a config with a non-default lookback.
    paths = _configured_paths(tmp_path)
    transport = _RecordingGoogleTransport()
    _patch_transport(monkeypatch, transport)
    clock: Clock = _FixedClock()
    with Store(paths.database, clock=clock) as store:
        access = open_source_access(paths, store, clock)

        # When: the composed reader prepares source snapshots.
        _ = access.readers.open(_FakeCredential()).prepare_evaluation()

    # Then: scheduled and lazy callers inherit the path-local setting.
    assert _gmail_query(transport)["q"] == _EXPECTED_QUERY


def test_google_smoke_rejects_invalid_lookback_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the smoke command's config has an invalid source lookback.
    paths = ProactivePaths.for_database(tmp_path / "state" / "proactive.db")
    paths.state_directory.mkdir()
    _ = paths.config.write_text(
        "[sources]\ngmail_lookback_days = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROACTIVE_DATABASE", str(paths.database))

    # When: the real CLI boundary starts the confirmed smoke path.
    result = cli.main(["google-smoke", "--confirm-real-account-read"])
    captured = capsys.readouterr()

    # Then: invalid user config is a redacted precondition error, not a traceback.
    assert result == 2
    assert captured.out == ""
    assert "invalid config gmail_lookback_days" in captured.err
    assert "Traceback" not in captured.err
