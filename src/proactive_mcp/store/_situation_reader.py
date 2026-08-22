"""Typed SQLite reads and row decoding for situations."""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from ._situation_models import (
    SITUATION_ADAPTER,
    Situation,
    SituationState,
    SituationType,
)
from ._situation_sql import (
    COUNT_DELIVERED_BETWEEN,
    SELECT_ACTIVE_BY_TYPE,
    SELECT_MUTED_TYPES,
    SELECT_SITUATION_BY_DEDUPE_KEY,
    SELECT_SITUATION_BY_ID,
    SELECT_SITUATIONS,
)

if TYPE_CHECKING:
    import sqlite3

_SITUATION_TYPES: tuple[SituationType, ...] = get_args(SituationType)


class SituationReader:
    """Decode situation query results captured through SQLite callbacks.

    The capture buffers are intentionally mutable because SQLite scalar
    callbacks cannot return structured Python records directly.
    """

    _connection: sqlite3.Connection
    _situations: list[Situation]
    _strings: list[str]
    _ints: list[int]

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._situations = []
        self._strings = []
        self._ints = []
        connection.create_function(
            "_proactive_capture_situation",
            1,
            self._capture_situation,
        )
        connection.create_function(
            "_proactive_capture_situation_str",
            1,
            self._capture_string,
        )
        connection.create_function(
            "_proactive_capture_situation_int",
            1,
            self._capture_int,
        )

    def active_by_type(self, situation_type: SituationType) -> tuple[Situation, ...]:
        return self._capture(SELECT_ACTIVE_BY_TYPE, (situation_type,))

    def capture_situations(
        self,
        sql: str,
        params: tuple[int | str | None, ...],
    ) -> tuple[Situation, ...]:
        """Decode a caller-owned situation projection through this reader.

        One reader per connection owns the capture callbacks, so every
        situation projection must be decoded here rather than re-registered.
        """
        return self._capture(sql, params)

    def muted_types(self) -> tuple[SituationType, ...]:
        self._strings.clear()
        _ = self._connection.execute(SELECT_MUTED_TYPES)
        return tuple(
            name
            for name in _SITUATION_TYPES
            for captured in self._strings
            if captured == name
        )

    def list_situations(
        self,
        state: SituationState | None = None,
    ) -> tuple[Situation, ...]:
        return self._capture(SELECT_SITUATIONS, (state, state))

    def get_situation(self, situation_id: int) -> Situation | None:
        situations = self._capture(SELECT_SITUATION_BY_ID, (situation_id,))
        return situations[0] if situations else None

    def situation_by_dedupe_key(self, dedupe_key: str) -> Situation | None:
        situations = self._capture(SELECT_SITUATION_BY_DEDUPE_KEY, (dedupe_key,))
        return situations[0] if situations else None

    def has_delivery(self, situation_id: int) -> bool:
        """Return whether immutable delivery history exists for a situation."""
        self._ints.clear()
        _ = self._connection.execute(
            """
            SELECT _proactive_capture_situation_int(COUNT(*))
            FROM situation_deliveries WHERE situation_id = ?
            """,
            (situation_id,),
        )
        return bool(self._ints and self._ints[0] > 0)

    def count_delivered_between(self, start: str, end: str) -> int:
        self._ints.clear()
        _ = self._connection.execute(
            f"SELECT _proactive_capture_situation_int(({COUNT_DELIVERED_BETWEEN}))",
            (start, end),
        )
        return self._ints[0] if self._ints else 0

    def _capture(
        self,
        sql: str,
        params: tuple[int | str | None, ...] = (),
    ) -> tuple[Situation, ...]:
        self._situations.clear()
        _ = self._connection.execute(sql, params)
        return tuple(self._situations)

    def _capture_situation(self, payload: str) -> int:
        situation = SITUATION_ADAPTER.validate_json(payload)
        self._situations.append(situation)
        return situation.id

    def _capture_string(self, value: str) -> int:
        self._strings.append(value)
        return 1

    def _capture_int(self, value: int | None) -> int:
        if value is not None:
            self._ints.append(value)
        return 0 if value is None else value
