from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from google.oauth2.credentials import Credentials
from keyring.errors import NoKeyringError, PasswordDeleteError

import proactive_mcp.sources as sources_module
import proactive_mcp.sources.credentials as credentials_module
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStorageError,
    CredentialStore,
    GoogleCredential,
)
from proactive_mcp.store import Store

if TYPE_CHECKING:
    from pathlib import Path

_UNAVAILABLE = "unavailable"
_TEST_ACCESS = "access" + "-token"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"
_EPOCH_A = "epoch" + "-a"
_EPOCH_B = "epoch" + "-b"
_TOMBSTONED = "must-not" + "-return"
_LEGACY_KEYRING_KEY = ("proactive-mcp", "google-readonly-oauth")


@dataclass(frozen=True, slots=True)
class FakeKeyring:
    """Record keyring calls while allowing availability to change."""

    passwords: dict[tuple[str, str], str] = field(default_factory=dict)
    unavailable: bool = False
    delete_fails: bool = False
    calls: list[str] = field(default_factory=list)

    def get_password(self, service_name: str, username: str) -> str | None:
        self.calls.append("get")
        if self.unavailable:
            raise NoKeyringError(_UNAVAILABLE)
        return self.passwords.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.calls.append("set")
        if self.unavailable:
            raise NoKeyringError(_UNAVAILABLE)
        self.passwords[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.calls.append("delete")
        if self.unavailable:
            raise NoKeyringError(_UNAVAILABLE)
        if self.delete_fails:
            raise PasswordDeleteError(_UNAVAILABLE)
        _ = self.passwords.pop((service_name, username), None)


def _credentials(
    *,
    refresh_token: str = _TEST_REFRESH,
    scopes: tuple[str, ...] = GOOGLE_READONLY_SCOPES,
) -> GoogleCredential:
    return Credentials(
        token=_TEST_ACCESS,
        refresh_token=refresh_token,
        token_uri=_TEST_TOKEN_URI,
        client_id=_TEST_CLIENT_ID,
        client_secret=_TEST_CLIENT_SECRET,
        scopes=list(scopes),
    )


def test_credentials_use_keyring_before_private_file(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path / "state", keyring=keyring)

    store.save(_credentials())
    loaded = store.load()

    assert loaded is not None
    assert loaded.refresh_token == _TEST_REFRESH
    assert tuple(loaded.scopes or ()) == GOOGLE_READONLY_SCOPES
    assert keyring.calls == ["set", "get"]
    assert not store.file_path.exists()


def test_credentials_are_isolated_by_state_root(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    first = CredentialStore(tmp_path / "first", keyring=keyring)
    second = CredentialStore(tmp_path / "second", keyring=keyring)
    second_refresh = "second-refresh" + "-token"

    first.save(_credentials())
    second.save(_credentials(refresh_token=second_refresh))

    assert first.keyring_username != second.keyring_username
    assert str(tmp_path) not in first.keyring_username
    first_loaded = first.load()
    second_loaded = second.load()
    assert first_loaded is not None
    assert first_loaded.refresh_token == _TEST_REFRESH
    assert second_loaded is not None
    assert second_loaded.refresh_token == second_refresh

    first.delete()

    assert first.load() is None
    remaining = second.load()
    assert remaining is not None
    assert remaining.refresh_token == second_refresh


def test_disconnect_google_sources_deletes_credential_then_auth_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured Google sources with one profile-bound keyring credential.
    database_path = tmp_path / "state" / "proactive.db"
    keyring = FakeKeyring()
    monkeypatch.setattr(credentials_module, "os_keyring", keyring)
    CredentialStore(database_path.parent).save(_credentials())
    with Store(database_path) as store:
        store.set_google_auth_state("configured")

    # When: credential-first rollback disconnects Google.
    sources_module.disconnect_google_sources(database_path)

    # Then: the credential is gone before both source states become unconfigured.
    assert CredentialStore(database_path.parent).load() is None
    with Store(database_path) as store:
        assert tuple(state.auth_state for state in store.list_source_sync()) == (
            "not_configured",
            "not_configured",
        )


def test_disconnect_google_sources_preserves_configured_state_on_delete_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: configured sources whose keyring refuses credential deletion.
    database_path = tmp_path / "state" / "proactive.db"
    keyring = FakeKeyring(delete_fails=True)
    monkeypatch.setattr(credentials_module, "os_keyring", keyring)
    credential_store = CredentialStore(database_path.parent)
    credential_store.save(_credentials())
    with Store(database_path) as store:
        store.set_google_auth_state("configured")

    # When: credential-first rollback cannot remove the keyring value.
    with pytest.raises(CredentialStorageError):
        sources_module.disconnect_google_sources(database_path)

    # Then: source state is preserved and the deletion tombstone remains.
    with Store(database_path) as store:
        assert tuple(state.auth_state for state in store.list_source_sync()) == (
            "configured",
            "configured",
        )
    assert credential_store.state_path.exists()


def test_default_state_migrates_previous_global_keyring_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "default-state"
    monkeypatch.setattr(credentials_module, "_DEFAULT_STATE_DIRECTORY", state)
    keyring = FakeKeyring()
    legacy = _credentials().to_json()
    keyring.passwords[_LEGACY_KEYRING_KEY] = legacy
    store = CredentialStore(state, keyring=keyring)

    loaded = store.load()
    loaded_again = CredentialStore(state, keyring=keyring).load()

    assert loaded is not None
    assert loaded.refresh_token == _TEST_REFRESH
    assert loaded_again is not None
    assert loaded_again.refresh_token == _TEST_REFRESH
    assert keyring.passwords[_LEGACY_KEYRING_KEY] != legacy
    assert _TEST_REFRESH not in keyring.passwords[_LEGACY_KEYRING_KEY]
    assert ("proactive-mcp", store.keyring_username) in keyring.passwords
    assert store.state_path.exists()


def test_custom_state_does_not_claim_ambiguous_global_keyring_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        credentials_module,
        "_DEFAULT_STATE_DIRECTORY",
        tmp_path / "default-state",
    )
    keyring = FakeKeyring()
    legacy = _credentials().to_json()
    keyring.passwords[_LEGACY_KEYRING_KEY] = legacy
    custom = CredentialStore(tmp_path / "custom-state", keyring=keyring)

    loaded = custom.load()

    assert loaded is None
    assert keyring.passwords[_LEGACY_KEYRING_KEY] == legacy
    assert ("proactive-mcp", custom.keyring_username) not in keyring.passwords
