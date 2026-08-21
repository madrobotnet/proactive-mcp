"""Situation engine: one deterministic evaluation pass over fresh inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from proactive_mcp.config import DetectorSettings
from proactive_mcp.store import evaluate_source_freshness

from .calendar_conflict import detect_calendar_conflicts
from .personal_occasion import detect_personal_occasions
from .reply_deadline import detect_reply_deadlines

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from proactive_mcp.clock import Clock
    from proactive_mcp.store import (
        Detection,
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
        gmail_freshness, calendar_freshness = self._freshness(now)
        occasion_detections = detect_personal_occasions(
            self._store.list_dated_memories(),
            now=now,
            tz=self._tz,
        )
        detections: list[Detection] = list(occasion_detections)
        if inputs.gmail_threads is not None:
            detections.extend(
                detect_reply_deadlines(
                    inputs.gmail_threads,
                    now=now,
                    tz=self._tz,
                    threshold=self._detectors.reply_threshold,
                )
            )
        if inputs.calendar_events is not None:
            detections.extend(
                detect_calendar_conflicts(
                    inputs.calendar_events,
                    now=now,
                    tz=self._tz,
                    critical_window=self._detectors.calendar_critical_window,
                    high_window=self._detectors.calendar_high_window,
                )
            )
        summary = situations.upsert_detections(detections)
        expired = situations.expire_lapsed()
        resolved = situations.resolve_absent(
            "personal_occasion",
            _keys(occasion_detections),
        )
        if inputs.gmail_threads is not None and _is_fresh(gmail_freshness):
            resolved += situations.resolve_absent(
                "reply_deadline",
                _keys_of_type(detections, "reply_deadline"),
            )
        if inputs.calendar_events is not None and _is_fresh(calendar_freshness):
            resolved += situations.resolve_absent(
                "calendar_conflict",
                _keys_of_type(detections, "calendar_conflict"),
            )
        warnings = _warnings(
            inputs,
            gmail=gmail_freshness,
            calendar=calendar_freshness,
        )
        return EvaluationResult(
            created=summary.created,
            reactivated=summary.reactivated,
            refreshed=summary.refreshed,
            resolved=resolved,
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


def _is_fresh(freshness: SourceFreshness) -> bool:
    return freshness.status == "ok"


def _warnings(
    inputs: EngineInputs,
    *,
    gmail: SourceFreshness,
    calendar: SourceFreshness,
) -> tuple[str, ...]:
    warnings: list[str] = []
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


def _keys(detections: tuple[Detection, ...]) -> tuple[str, ...]:
    return tuple(detection.dedupe_key for detection in detections)


def _keys_of_type(
    detections: list[Detection],
    situation_type: str,
) -> tuple[str, ...]:
    return tuple(
        detection.dedupe_key
        for detection in detections
        if detection.situation_type == situation_type
    )
