from __future__ import annotations

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
            body = b'{"threads":[]}'
        else:
            body = b'{"items":[]}'
        return GoogleHttpResponse(status_code=200, body=body)


@dataclass(frozen=True, slots=True)
class _TransportFactory:
    transport: _RecordingGoogleTransport

    def __call__(self, _credential: GoogleCredential) -> _RecordingGoogleTransport:
        return self.transport


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
