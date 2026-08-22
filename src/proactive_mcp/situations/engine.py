"""Situation engine: one deterministic evaluation pass over fresh inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from proactive_mcp.config import DetectorSettings
from proactive_mcp.store import (
    DelayedSourceGenerationError,
    evaluate_source_freshness,
)

from .calendar_conflict import run_calendar_conflict_detection
from .personal_occasion import detect_personal_occasions
from .reply_deadline import detect_reply_deadlines

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from proactive_mcp.clock import Clock
    from proactive_mcp.store import (
        DetectionApplySummary,
        SourceFreshness,
        SourceName,
        Store,
    )

    from .inputs import EngineInputs

__all__ = ["EvaluationResult", "SituationEngine"]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """The observable outcome of one evaluation pass.

    ``warnings`` and the per-source freshness are always populated so a
    caller can never present an empty situation list as an all-clear while
    a source is stale, failed, or was skipped this pass.
    """

    created: int
    reactivated: int
    refreshed: int
    resolved: int
    expired: int
    woken: int
    warnings: tuple[str, ...]
    gmail_freshness: SourceFreshness
    calendar_freshness: SourceFreshness


class SituationEngine:
    """Run detectors over fresh snapshots and keep stored situations true."""

    _store: Store
    _clock: Clock
    _tz: tzinfo
    _detectors: DetectorSettings

    def __init__(
        self,
        store: Store,
        clock: Clock,
        tz: tzinfo,
        detectors: DetectorSettings | None = None,
    ) -> None:
        """Bind the engine to storage, a clock, a timezone, and thresholds."""
        self._store = store
        self._clock = clock
        self._tz = tz
        self._detectors = detectors if detectors is not None else DetectorSettings()

    def evaluate(self, inputs: EngineInputs) -> EvaluationResult:
        """Evaluate one pass: wake, detect, upsert, expire, resolve, report.

        Detection and natural resolution for a source run only when its
        snapshot is present; a skipped source keeps its stored situations
        and contributes a warning instead of a false all-clear.
        """
        now = self._clock.now()
        situations = self._store.situations
        woken = situations.wake_snoozed()
        expired = situations.expire_lapsed()
        occasion_detections = detect_personal_occasions(
            self._store.list_dated_memories(),
            now=now,
            tz=self._tz,
            default_lead_days=self._detectors.occasion_default_lead_days,
        )
        applied: list[DetectionApplySummary] = [
            situations.apply_local_detections(occasion_detections)
        ]
        source_warnings: list[str] = []
        gmail = inputs.gmail_threads
        if gmail is not None:
            detections = detect_reply_deadlines(
                gmail.items,
                now=now,
                tz=self._tz,
                threshold=self._detectors.reply_threshold,
            )
            try:
                applied.append(
                    situations.apply_source_generation(
                        gmail.generation,
                        detections,
                        status="complete" if gmail.complete else "degraded",
                        sync_cursor=gmail.sync_cursor,
                        error_code=gmail.error_code,
                    )
                )
            except DelayedSourceGenerationError:
                source_warnings.append("gmail: delayed source generation ignored")
            source_warnings.extend(f"gmail: {code}" for code in gmail.warning_codes)
        calendar = inputs.calendar_events
        if calendar is not None:
            run = run_calendar_conflict_detection(
                calendar.items,
                now=now,
                tz=self._tz,
                critical_window=self._detectors.calendar_critical_window,
                high_window=self._detectors.calendar_high_window,
            )
            complete = calendar.complete and run.resolution_safe
            try:
                applied.append(
                    situations.apply_source_generation(
                        calendar.generation,
                        run.detections,
                        status="complete" if complete else "degraded",
                        sync_cursor=calendar.sync_cursor,
                        error_code=calendar.error_code,
                    )
                )
            except DelayedSourceGenerationError:
                source_warnings.append("calendar: delayed source generation ignored")
            source_warnings.extend(
                f"calendar: {code}" for code in calendar.warning_codes
            )
            source_warnings.extend(f"calendar: {code}" for code in run.warning_codes)
        gmail_freshness, calendar_freshness = self._freshness(now)
        warnings = _warnings(
            inputs,
            gmail=gmail_freshness,
            calendar=calendar_freshness,
            source_warnings=tuple(source_warnings),
        )
        return EvaluationResult(
            created=sum(item.upsert.created for item in applied),
            reactivated=sum(item.upsert.reactivated for item in applied),
            refreshed=sum(item.upsert.refreshed for item in applied),
            resolved=sum(item.resolved for item in applied),
            expired=expired,
            woken=woken,
            warnings=warnings,
            gmail_freshness=gmail_freshness,
            calendar_freshness=calendar_freshness,
        )

    def _freshness(
        self,
        now: datetime,
    ) -> tuple[SourceFreshness, SourceFreshness]:
        gmail_state, calendar_state = self._store.list_source_sync()
        return (
            evaluate_source_freshness(gmail_state, now),
            evaluate_source_freshness(calendar_state, now),
        )


def _warnings(
    inputs: EngineInputs,
    *,
    gmail: SourceFreshness,
    calendar: SourceFreshness,
    source_warnings: tuple[str, ...],
) -> tuple[str, ...]:
    warnings = list(source_warnings)
    skipped: list[tuple[SourceName, bool]] = [
        ("gmail", inputs.gmail_threads is None),
        ("calendar", inputs.calendar_events is None),
    ]
    for source, was_skipped in skipped:
        if was_skipped:
            warnings.append(
                f"{source}: skipped this pass (no snapshot); situations kept"
            )
    for source, freshness in (("gmail", gmail), ("calendar", calendar)):
        if freshness.status != "ok":
            warnings.append(f"{source}: source is {freshness.status}")
    return tuple(warnings)
