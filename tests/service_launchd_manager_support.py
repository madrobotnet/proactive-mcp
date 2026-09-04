from __future__ import annotations

from proactive_mcp.cli.service_launchd import LaunchctlResult


class FakeLaunchctlRunner:
    """Record launchctl argv while serving mutable deterministic responses."""

    responses: dict[tuple[str, ...], LaunchctlResult]
    recorded_calls: list[tuple[str, ...]]

    def __init__(
        self,
        responses: dict[tuple[str, ...], LaunchctlResult] | None = None,
    ) -> None:
        self.responses = {} if responses is None else responses
        self.recorded_calls = []

    def run(self, *argv: str) -> LaunchctlResult:
        self.recorded_calls.append(argv)
        return self.responses.get(
            argv,
            LaunchctlResult(succeeded=True, output="", exit_code=0),
        )
