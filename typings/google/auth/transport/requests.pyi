from collections.abc import Mapping
from typing import Literal, Protocol

from proactive_mcp.sources.credentials import GoogleCredential

class _Response(Protocol):
    status_code: int
    content: bytes

class AuthorizedSession:
    def __init__(self, credentials: GoogleCredential) -> None: ...
    def request(
        self,
        method: Literal["GET"],
        url: str,
        *,
        params: Mapping[str, str],
        timeout: int,
    ) -> _Response: ...
