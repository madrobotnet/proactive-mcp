from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from google.oauth2.credentials import Credentials
from keyring.errors import NoKeyringError, PasswordDeleteError

import proactive_mcp.sources.credentials as credentials_module
from proactive_mcp.sources.credentials import (
    GOOGLE_READONLY_SCOPES,
    CredentialStorageError,
    CredentialStore,
    GoogleCredential,
)

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


def test_default_state_rejects_overbroad_global_keyring_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "default-state"
    monkeypatch.setattr(credentials_module, "_DEFAULT_STATE_DIRECTORY", state)
    keyring = FakeKeyring()
    legacy = _credentials(
        scopes=(
            *GOOGLE_READONLY_SCOPES,
            "https://www.googleapis.com/auth/gmail.modify",
        )
    ).to_json()
    keyring.passwords[_LEGACY_KEYRING_KEY] = legacy
    store = CredentialStore(state, keyring=keyring)

    loaded = store.load()

    assert loaded is None
    assert keyring.passwords == {_LEGACY_KEYRING_KEY: legacy}
    assert not store.state_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="0600 fallback is POSIX-only")
def test_credentials_fall_back_to_a_0600_file_only_without_keyring(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring(unavailable=True))

    store.save(_credentials())

    assert stat.S_IMODE(store.file_path.stat().st_mode) == 0o600
    assert store.file_path.parent == tmp_path / "state" / "credentials"
    loaded = store.load()
    assert loaded is not None
    assert tuple(loaded.scopes or ()) == GOOGLE_READONLY_SCOPES


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow fallback behavior")
def test_credentials_reject_a_symlinked_fallback_parent(tmp_path: Path) -> None:
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    state = tmp_path / "state"
    state.symlink_to(attacker, target_is_directory=True)
    store = CredentialStore(state, keyring=FakeKeyring(unavailable=True))

    with pytest.raises(CredentialStorageError):
        store.save(_credentials())

    assert tuple(attacker.iterdir()) == ()


def test_credentials_reject_persisted_scope_escalation(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path / "state", keyring=keyring)
    store.save(_credentials())
    key = next(iter(keyring.passwords))
    keyring.passwords[key] = _credentials(
        scopes=(
            *GOOGLE_READONLY_SCOPES,
            "https://www.googleapis.com/auth/gmail.modify",
        )
    ).to_json()

    loaded = store.load()

    assert loaded is None


def test_credentials_expose_failed_keyring_deletion(tmp_path: Path) -> None:
    keyring = FakeKeyring(delete_fails=True)
    store = CredentialStore(tmp_path / "state", keyring=keyring)
    store.save(_credentials())

    with pytest.raises(CredentialStorageError):
        store.delete()

    assert keyring.passwords


def test_new_fallback_epoch_supersedes_stale_keyring_after_recovery(
    tmp_path: Path,
) -> None:
    keyring = FakeKeyring()
    state = tmp_path / "state"
    original = CredentialStore(state, keyring=keyring)
    original.save(_credentials(refresh_token=_EPOCH_A))
    assert original.load() is not None

    object.__setattr__(keyring, "unavailable", True)
    replacement = CredentialStore(state, keyring=keyring)
    replacement.save(_credentials(refresh_token=_EPOCH_B))
    object.__setattr__(keyring, "unavailable", False)

    original.delete()
    loaded = CredentialStore(state, keyring=keyring).load()

    assert loaded is not None
    assert loaded.refresh_token == _EPOCH_B


def test_unavailable_keyring_deletion_fails_with_tombstone_preserved(
    tmp_path: Path,
) -> None:
    keyring = FakeKeyring()
    state = tmp_path / "state"
    store = CredentialStore(state, keyring=keyring)
    store.save(_credentials(refresh_token=_TOMBSTONED))

    object.__setattr__(keyring, "unavailable", True)
    with pytest.raises(CredentialStorageError):
        store.delete()

    assert store.state_path.exists()
    assert keyring.passwords

    object.__setattr__(keyring, "unavailable", False)

    assert CredentialStore(state, keyring=keyring).load() is None
    assert keyring.passwords == {}


@pytest.mark.skipif(os.name == "nt", reason="0600 fallback is POSIX-only")
def test_corrupt_saved_credentials_are_ignored_without_exposing_payload(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path / "state", keyring=FakeKeyring(unavailable=True))
    _ = store.file_path.parent.mkdir(mode=0o700, parents=True)
    _ = store.file_path.write_text('{"refresh_token":"secret-value"}')
    store.file_path.chmod(0o600)

    loaded = store.load()

    assert loaded is None
