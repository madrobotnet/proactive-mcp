"""Google credential contracts and bounded serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, TypedDict

from google.oauth2.credentials import Credentials
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Sequence

GOOGLE_READONLY_SCOPES: Final[tuple[str, str]] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)
OAUTH_ENDPOINT: Final = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True, slots=True)
class MissingRefreshTokenError(Exception):
    @override
    def __str__(self) -> str:
        return "Google authorization did not provide a refresh token"


@dataclass(frozen=True, slots=True)
class CredentialScopeError(Exception):
    @override
    def __str__(self) -> str:
        return "Google authorization scopes are not the required read-only scopes"


@dataclass(frozen=True, slots=True)
class CredentialStorageError(Exception):
    @override
    def __str__(self) -> str:
        return "Google credential storage is unavailable"


class GoogleCredential(Protocol):
    @property
    def refresh_token(self) -> str | None: ...

    @property
    def scopes(self) -> Sequence[str] | None: ...

    def to_json(self) -> str: ...


class CredentialKeyring(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class GoogleAuthorizedUserInfo(TypedDict):
    client_id: str
    client_secret: str
    refresh_token: str
    token: str | None


class AuthorizedUserWire(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    client_id: str
    client_secret: str
    refresh_token: str
    token: str | None = None
    scopes: tuple[str, ...]

    def as_google_authorized_user_info(self) -> GoogleAuthorizedUserInfo:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "token": self.token,
        }


AUTHORIZED_USER_ADAPTER: Final[TypeAdapter[AuthorizedUserWire]] = TypeAdapter(
    AuthorizedUserWire
)


class CredentialEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    epoch: int = Field(ge=1)
    revision: str = Field(min_length=64, max_length=64)
    credential: str


class CredentialState(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    epoch: int = Field(ge=1)
    revision: str = Field(min_length=64, max_length=64)
    backend: Literal["keyring", "file", "deleted"]
    tombstone: bool = False


ENVELOPE_ADAPTER: Final[TypeAdapter[CredentialEnvelope]] = TypeAdapter(
    CredentialEnvelope
)
STATE_ADAPTER: Final[TypeAdapter[CredentialState]] = TypeAdapter(CredentialState)


def require_refreshable_readonly_credentials(credentials: GoogleCredential) -> None:
    if credentials.refresh_token is None or credentials.refresh_token == "":
        raise MissingRefreshTokenError
    if tuple(credentials.scopes or ()) != GOOGLE_READONLY_SCOPES:
        raise CredentialScopeError


def parse_credentials(serialized: str) -> GoogleCredential | None:
    try:
        wire = AUTHORIZED_USER_ADAPTER.validate_json(serialized)
    except ValidationError:
        return None
    if wire.scopes != GOOGLE_READONLY_SCOPES:
        return None
    try:
        credentials = Credentials(
            token=wire.token,
            refresh_token=wire.refresh_token,
            token_uri=OAUTH_ENDPOINT,
            client_id=wire.client_id,
            client_secret=wire.client_secret,
            scopes=GOOGLE_READONLY_SCOPES,
        )
    except ValueError:
        return None
    try:
        require_refreshable_readonly_credentials(credentials)
    except (CredentialScopeError, MissingRefreshTokenError):
        return None
    return credentials


def parse_enveloped_credentials(
    serialized: str,
    expected_epoch: int,
    expected_revision: str,
) -> GoogleCredential | None:
    envelope = parse_envelope(serialized)
    if envelope is None:
        return None
    if envelope.epoch != expected_epoch or envelope.revision != expected_revision:
        return None
    return parse_credentials(envelope.credential)


def parse_envelope(serialized: str) -> CredentialEnvelope | None:
    try:
        return ENVELOPE_ADAPTER.validate_json(serialized)
    except ValidationError:
        return None
