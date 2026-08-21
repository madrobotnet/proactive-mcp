"""Situation detection package: detectors, attention policy, and engine."""

from .calendar_conflict import (
    DEFAULT_CRITICAL_WINDOW,
    DEFAULT_HIGH_WINDOW,
    detect_calendar_conflicts,
)
from .engine import EvaluationResult, SituationEngine
from .inputs import EngineInputs, InboxThreadSnapshot
from .personal_occasion import DEFAULT_LEAD_DAYS, detect_personal_occasions
from .policy import AttentionPolicy, is_quiet_time
from .reply_deadline import DEFAULT_REPLY_THRESHOLD, detect_reply_deadlines

__all__ = [
    "DEFAULT_CRITICAL_WINDOW",
    "DEFAULT_HIGH_WINDOW",
    "DEFAULT_LEAD_DAYS",
    "DEFAULT_REPLY_THRESHOLD",
    "AttentionPolicy",
    "EngineInputs",
    "EvaluationResult",
    "InboxThreadSnapshot",
    "SituationEngine",
    "detect_calendar_conflicts",
    "detect_personal_occasions",
    "detect_reply_deadlines",
    "is_quiet_time",
]
