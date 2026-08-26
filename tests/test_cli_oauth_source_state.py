import pickle
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from proactive_mcp import sources
from proactive_mcp.sources import (
    GoogleOAuthAuthorizer,
    GoogleSetupOptions,
)
from proactive_mcp.sources._google_setup import (
    GoogleSourceConfigurationError as ExtractedGoogleSourceConfigurationError,
)
from proactive_mcp.sources.credentials import CredentialStore
from tests.cli_oauth_test_support import (
    FIXTURES,
    FakeFlowFactory,
    FakeInstalledAppFlow,
    FakeKeyring,
    count_authorization_url_events,
    count_setup_success_events,
    google_credential,
)


def test_configuration_error_retains_public_module_identity_after_extraction() -> None:
    exception_type = sources.GoogleSourceConfigurationError

    serialized_type = pickle.dumps(exception_type)

    assert exception_type.__module__ == "proactive_mcp.sources"
    assert exception_type is ExtractedGoogleSourceConfigurationError
    assert pickle.loads(serialized_type) is exception_type  # noqa: S301


def test_setup_emits_no_success_when_database_open_fails_after_credential_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credential_stores: list[CredentialStore] = []
    factory = FakeFlowFactory(FakeInstalledAppFlow(google_credential()))

    def credential_store(path: Path) -> CredentialStore:
        store = CredentialStore(path, keyring=FakeKeyring())
        credential_stores.append(store)
        return store

    database_error = OSError("database-/private/path-canary")

    class DatabaseOpenFailure:
        def __init__(self, _path: Path) -> None:
            raise database_error

    def authorizer(store: CredentialStore) -> GoogleOAuthAuthorizer:
        return GoogleOAuthAuthorizer(store, flow_factory=factory)

    monkeypatch.setattr(sources, "CredentialStore", credential_store)
    monkeypatch.setattr(sources, "GoogleOAuthAuthorizer", authorizer)
    monkeypatch.setattr(sources, "Store", DatabaseOpenFailure)

    with pytest.raises(sources.GoogleSourceConfigurationError) as raised:
        sources.configure_google_sources(
            tmp_path / "state.db",
            GoogleSetupOptions(
                client_secrets_path=FIXTURES / "installed-client.json",
                reauth=False,
                headless=True,
            ),
        )
    captured = capsys.readouterr()

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert credential_stores[0].load() is not None
    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 0


def test_setup_emits_no_success_when_source_state_write_fails_after_credential_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = FakeFlowFactory(FakeInstalledAppFlow(google_credential()))

    state_error = RuntimeError("source-state-canary")

    class SourceStateFailure:
        def __init__(self, _path: Path) -> None:
            pass

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
            raise state_error

    def credential_store(path: Path) -> CredentialStore:
        return CredentialStore(path, keyring=FakeKeyring())

    def authorizer(store: CredentialStore) -> GoogleOAuthAuthorizer:
        return GoogleOAuthAuthorizer(store, flow_factory=factory)

    monkeypatch.setattr(sources, "CredentialStore", credential_store)
    monkeypatch.setattr(sources, "GoogleOAuthAuthorizer", authorizer)
    monkeypatch.setattr(sources, "Store", SourceStateFailure)

    with pytest.raises(RuntimeError, match="source-state-canary"):
        sources.configure_google_sources(
            tmp_path / "state.db",
            GoogleSetupOptions(
                client_secrets_path=FIXTURES / "installed-client.json",
                reauth=False,
                headless=True,
            ),
        )
    captured = capsys.readouterr()

    assert count_authorization_url_events(captured.out, captured.err) == 1
    assert count_setup_success_events(captured.out, captured.err) == 0
