from proactive_mcp.sources.credentials import GoogleCredential
from proactive_mcp.sources.oauth import GoogleClientConfig

class WSGITimeoutError(Exception): ...

class InstalledAppFlow:
    @property
    def credentials(self) -> GoogleCredential: ...
    @classmethod
    def from_client_config(
        cls,
        client_config: GoogleClientConfig,
        scopes: tuple[str, str],
    ) -> InstalledAppFlow: ...
    def run_local_server(
        self,
        *,
        host: str,
        port: int,
        open_browser: bool,
        timeout_seconds: int,
        prompt: str | None = None,
    ) -> GoogleCredential: ...
