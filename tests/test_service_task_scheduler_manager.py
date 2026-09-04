from __future__ import annotations

from base64 import b64decode

from tests.windows_service_support import RecordingTaskSchedulerRunner, load_backend


def test_definition_query_handles_absent_task_without_typed_com_catch() -> None:
    backend = load_backend()
    runner = RecordingTaskSchedulerRunner()
    manager = backend.WindowsTaskSchedulerManager(runner=runner)

    assert manager.definition() is None

    script = b64decode(runner.calls[0][-1], validate=True).decode("utf-16-le")
    assert ".GetTasks(0)" in script
    assert ".GetTask(" not in script
