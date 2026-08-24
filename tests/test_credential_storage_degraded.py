from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from google.oauth2.credentials import Credentials
from keyring.errors import NoKeyringError

from proactive_mcp.delivery.evaluation import (
    EvaluationDependencies,
    EvaluationService,
    SkippedSources,
)
from proactive_mcp.situations.engine import SituationEngine
from proactive_mcp.sources.credentials import GOOGLE_READONLY_SCOPES, CredentialStore
from proactive_mcp.sources.lazy_sync import (
    LazySourceProvider,
    LazySyncPolicy,
    SourceAccess,
)
from proactive_mcp.store import Store
from tests.daemon_test_support import (
    FakeReaderFactory,
    StoreBackedReader,
    birthday_memory,
)
from tests.situation_test_support import FakeClock, utc_datetime

if TYPE_CHECKING:
    from pathlib import Path

    from proactive_mcp.sources.credentials import GoogleCredential

_UNAVAILABLE = "unavailable"
_POLL_INTERVAL = timedelta(minutes=5)
_TEST_ACCESS = "access" + "-token"
_TEST_REFRESH = "refresh" + "-token"
_TEST_TOKEN_URI = "https://oauth2.googleapis.test" + "/token"
_TEST_CLIENT_ID = "test-client" + ".apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client" + "-secret"


@dataclass(frozen=True, slots=True)
class FakeKeyring:
    """Record keyring calls while allowing availability to change after save."""

    passwords: dict[tuple[str, str], str] = field(default_factory=dict)
    unavailable: bool = False
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
        _ = self.passwords.pop((service_name, username), None)


def _credentials() -> GoogleCredential:
    return Credentials(
        token=_TEST_ACCESS,
        refresh_token=_TEST_REFRESH,
        token_uri=_TEST_TOKEN_URI,
        client_id=_TEST_CLIENT_ID,
        client_secret=_TEST_CLIENT_SECRET,
        scopes=list(GOOGLE_READONLY_SCOPES),
    )


def test_lost_keyring_after_save_degrades_evaluation_instead_of_raising(
    tmp_path: Path,
) -> None:
    # Given: configured sources whose credential was saved to the keyring,
    # then keyring access disappeared, plus a local D-7 memory.
    clock = FakeClock(utc_datetime(2026, 7, 11, 9))
    keyring = FakeKeyring()
    credentials = CredentialStore(tmp_path / "state", keyring=keyring)
    credentials.save(_credentials())
    assert keyring.calls == ["set"]
    assert keyring.passwords
    assert not credentials.file_path.exists()
    object.__setattr__(keyring, "unavailable", True)

    with Store(tmp_path / "proactive.db", clock=clock) as store:
        store.set_google_auth_state("configured")
        _ = store.remember(birthday_memory())
        reader = StoreBackedReader(store=store)
        service = EvaluationService(
            EvaluationDependencies(
                evaluator=SituationEngine(store, clock, UTC),
                sources=LazySourceProvider(
                    access=SourceAccess(
                        sync_state=store,
                        credentials=credentials,
                        readers=FakeReaderFactory(reader=reader),
                    ),
                    liveness=store.daemon,
                    clock=clock,
                    policy=LazySyncPolicy.for_poll_interval(_POLL_INTERVAL),
                ),
            )
        )

        # When: a tool-time evaluation runs after the keyring-backed load fails.
        completed = service.run_once()
        detected = store.situations.list_situations()

    # Then: the pass returns a typed skip with an actionable storage warning
    # instead of raising CredentialStorageError, and local truth still evaluates.
    assert isinstance(completed.sources, SkippedSources)
    assert completed.sources.reason == "credential_storage_unavailable"
    assert reader.reads == []
    assert tuple(item.situation_type for item in detected) == ("personal_occasion",)
    assert any(
        warning.startswith("google:")
        and "credential storage is unavailable" in warning.casefold()
        for warning in completed.warnings
    )
