import subprocess
import sys
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from proactive_mcp.server.situation_responses import SourceReadDiagnosticsResponse
from proactive_mcp.store import SourceErrorCode


class LegacySmokeSource(BaseModel):
    """Pre-Todo-7 source parser used to prove additive compatibility."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    count: int
    error_code: SourceErrorCode | None


class LegacySmokeResponse(BaseModel):
    """Pre-Todo-7 smoke parser, which ignores additive fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: LegacySmokeSource
    calendar: LegacySmokeSource
    credential_cleanup_failed: bool


class CurrentSmokeGmail(LegacySmokeSource):
    """Typed additive Gmail smoke shape used for privacy assertions."""

    diagnostics: SourceReadDiagnosticsResponse


class CurrentSmokeResponse(BaseModel):
    """Typed current smoke response preserving every legacy field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    gmail: CurrentSmokeGmail
    calendar: LegacySmokeSource
    credential_cleanup_failed: bool


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "proactive_mcp", *args]
    return subprocess.run(command, capture_output=True, text=True, env=env, check=False)
