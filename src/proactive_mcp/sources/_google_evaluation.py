"""Build detector inputs from one authenticated Google source read."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

from proactive_mcp.situations.inputs import EngineInputs, SourceSnapshot
from proactive_mcp.sources._credential_models import CredentialStorageError
from proactive_mcp.sources.calendar import CalendarError
from proactive_mcp.sources.gmail import GmailError
from proactive_mcp.store.sync import source_failure_diagnostics

if TYPE_CHECKING:
    from collections.abc import Callable

    from proactive_mcp.sources.calendar import CalendarReadResult
    from proactive_mcp.sources.gmail import GmailInboxReadResult
    from proactive_mcp.store import SourceGeneration, Store
    from proactive_mcp.store.sync import (
        SourceReadDiagnostics,
        SourceSyncFailureCode,
    )


class _GmailReader(Protocol):
    def read_inbox_threads(self) -> GmailInboxReadResult: ...


class _CalendarReader(Protocol):
    def list_events(self) -> CalendarReadResult: ...


class _Credentials(Protocol):
    def delete(self) -> None: ...


class EvaluationDependencies(Protocol):
    @property
    def store(self) -> Store: ...

    @property
    def gmail(self) -> _GmailReader: ...

    @property
    def calendar(self) -> _CalendarReader: ...

    @property
    def credentials(self) -> _Credentials: ...


def prepare_evaluation(
    dependencies: EvaluationDependencies,
    invalid_grant_error: type[Exception],
    transport_error: type[Exception],
    diagnostics_builder: Callable[[GmailInboxReadResult], SourceReadDiagnostics],
    transport_error_code: Callable[[Exception], SourceSyncFailureCode],
) -> EngineInputs:
    """Read ordered source snapshots without accepting their truth yet."""
    store = dependencies.store
    gmail_generation = store.reserve_source_generation("gmail")
    calendar_generation = store.reserve_source_generation("calendar")
    try:
        gmail_result = dependencies.gmail.read_inbox_threads()
    except invalid_grant_error:
        return _invalid_grant_inputs(
            dependencies, gmail_generation, calendar_generation
        )
    except (GmailError, transport_error) as error:
        error_code = (
            error.error_code
            if isinstance(error, GmailError)
            else transport_error_code(error)
        )
        gmail_snapshot = SourceSnapshot(
            generation=gmail_generation,
            items=(),
            complete=False,
            warning_codes=(f"gmail_{error_code}",),
            error_code=error_code,
        )
        gmail_read_diagnostics = source_failure_diagnostics(error_code)
    else:
        gmail_snapshot = SourceSnapshot(
            generation=gmail_generation,
            items=gmail_result.threads,
            complete=gmail_result.coverage_complete,
            sync_cursor=gmail_result.provider_history_cursor,
            warning_codes=tuple(gmail_result.degradation_reasons),
            resolve_absent=gmail_result.allows_absent_resolution,
            resolution_scope_ids=gmail_result.resolution_safe_thread_ids,
            resolution_excluded_ids=gmail_result.resolution_excluded_thread_ids,
        )
        gmail_read_diagnostics = diagnostics_builder(gmail_result)
    try:
        calendar_result = dependencies.calendar.list_events()
    except invalid_grant_error:
        return _invalid_grant_inputs(
            dependencies, gmail_generation, calendar_generation
        )
    except (CalendarError, transport_error) as error:
        error_code = (
            error.error_code
            if isinstance(error, CalendarError)
            else transport_error_code(error)
        )
        calendar_snapshot = SourceSnapshot(
            generation=calendar_generation,
            items=(),
            complete=False,
            warning_codes=(f"calendar_{error_code}",),
            error_code=error_code,
        )
    else:
        calendar_snapshot = SourceSnapshot(
            generation=calendar_generation,
            items=calendar_result.events,
            complete=calendar_result.skipped_count == 0,
            warning_codes=(
                ()
                if calendar_result.skipped_count == 0
                else ("calendar_skipped_items",)
            ),
        )
    return EngineInputs(
        gmail_threads=gmail_snapshot,
        calendar_events=calendar_snapshot,
        gmail_diagnostics=gmail_read_diagnostics,
    )


def _invalid_grant_inputs(
    dependencies: EvaluationDependencies,
    gmail_generation: SourceGeneration,
    calendar_generation: SourceGeneration,
) -> EngineInputs:
    with suppress(CredentialStorageError):
        dependencies.credentials.delete()
    return EngineInputs(
        gmail_threads=SourceSnapshot(
            generation=gmail_generation,
            items=(),
            complete=False,
            warning_codes=("gmail_invalid_grant",),
            error_code="invalid_grant",
        ),
        calendar_events=SourceSnapshot(
            generation=calendar_generation,
            items=(),
            complete=False,
            warning_codes=("calendar_invalid_grant",),
            error_code="invalid_grant",
        ),
        gmail_diagnostics=source_failure_diagnostics("invalid_grant"),
    )
