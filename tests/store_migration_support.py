"""Typed SQLite helpers shared by store migration tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias, TypeVar

from pydantic import TypeAdapter

if TYPE_CHECKING:
    import sqlite3

T = TypeVar("T")
MigratedMemoryTuple: TypeAlias = tuple[
    int,
    str,
    str | None,
    str,
    str | None,
    str,
    int | None,
    str,
    str,
    str,
    int,
    str,
    int | None,
]


@dataclass(frozen=True, slots=True)
class _ProjectedMemoryRow:
    memory_id: int
    kind: str
    entity: str | None
    content: str
    date_anchor: str | None
    recurrence: str
    lead_days: int | None
    source: str
    created_at: str
    updated_at: str
    archived: int
    attribute: str
    supersedes_id: int | None


_STRING_PAIR_ADAPTER: Final[TypeAdapter[tuple[str, str]]] = TypeAdapter(tuple[str, str])
_DEFAULT_ADAPTER: Final[TypeAdapter[tuple[str, str | None]]] = TypeAdapter(
    tuple[str, str | None]
)
_INDEX_ADAPTER: Final[TypeAdapter[tuple[str, int, int]]] = TypeAdapter(
    tuple[str, int, int]
)
_FOREIGN_KEY_ADAPTER: Final[TypeAdapter[tuple[str, str, str, str]]] = TypeAdapter(
    tuple[str, str, str, str]
)
_PROJECTED_ROW_ADAPTER: Final[TypeAdapter[_ProjectedMemoryRow]] = TypeAdapter(
    _ProjectedMemoryRow
)


def capture_strings(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[str, ...] = (),
) -> list[str]:
    values: list[str] = []

    def capture(value: str) -> int:
        values.append(value)
        return 0

    connection.create_function("_cap_str", 1, capture)
    _ = connection.execute(sql, params)
    return values


def capture_ints(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[str, ...] = (),
) -> list[int]:
    values: list[int] = []

    def capture(value: int) -> int:
        values.append(value)
        return 0

    connection.create_function("_cap_int", 1, capture)
    _ = connection.execute(sql, params)
    return values


def capture_optional_ints(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[str, ...] = (),
) -> list[int | None]:
    values: list[int | None] = []

    def capture(value: int | None) -> int:
        values.append(value)
        return 0

    connection.create_function("_cap_opt_int", 1, capture)
    _ = connection.execute(sql, params)
    return values


def capture_json_rows(
    connection: sqlite3.Connection,
    adapter: TypeAdapter[T],
    sql: str,
    params: tuple[str, ...] = (),
) -> list[T]:
    rows: list[T] = []

    def capture(payload: str) -> int:
        rows.append(adapter.validate_json(payload))
        return 0

    connection.create_function("_cap_json", 1, capture)
    _ = connection.execute(sql, params)
    return rows


def migrated_memory_projection(
    connection: sqlite3.Connection,
) -> list[MigratedMemoryTuple]:
    rows: list[MigratedMemoryTuple] = []
    rows.extend(
        (
            row.memory_id,
            row.kind,
            row.entity,
            row.content,
            row.date_anchor,
            row.recurrence,
            row.lead_days,
            row.source,
            row.created_at,
            row.updated_at,
            row.archived,
            row.attribute,
            row.supersedes_id,
        )
        for row in capture_json_rows(
            connection,
            _PROJECTED_ROW_ADAPTER,
            """
            SELECT SUM(_cap_json(json_object(
                'memory_id', m.id,
                'kind', m.kind,
                'entity', e.label,
                'content', m.content,
                'date_anchor', m.date_anchor,
                'recurrence', m.recurrence,
                'lead_days', m.lead_days,
                'source', m.source,
                'created_at', m.created_at,
                'updated_at', m.updated_at,
                'archived', m.archived,
                'attribute', m.attribute,
                'supersedes_id', m.supersedes_id
            )))
            FROM memory_items AS m
            LEFT JOIN entities AS e ON e.id = m.entity_id
            ORDER BY m.id
            """,
        )
    )
    return rows


def alias_norms(connection: sqlite3.Connection) -> set[str]:
    return set(
        capture_strings(
            connection,
            "SELECT SUM(_cap_str(alias_norm)) FROM entity_aliases",
        )
    )


def table_names(connection: sqlite3.Connection) -> set[str]:
    return set(
        capture_strings(
            connection,
            "SELECT SUM(_cap_str(name)) FROM sqlite_master WHERE type = 'table'",
        )
    )


def column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return set(
        capture_strings(
            connection,
            "SELECT SUM(_cap_str(name)) FROM pragma_table_info(?)",
            (table,),
        )
    )


def column_defaults(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, str | None]:
    rows = capture_json_rows(
        connection,
        _DEFAULT_ADAPTER,
        """
        SELECT SUM(_cap_json(json_array(name, dflt_value)))
        FROM pragma_table_info(?)
        """,
        (table,),
    )
    return dict(rows)


def index_flags(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, tuple[int, int]]:
    rows = capture_json_rows(
        connection,
        _INDEX_ADAPTER,
        """
        SELECT SUM(_cap_json(json_array(name, "unique", partial)))
        FROM pragma_index_list(?)
        """,
        (table,),
    )
    return {name: (unique, partial) for name, unique, partial in rows}


def foreign_keys(
    connection: sqlite3.Connection,
    table: str,
) -> set[tuple[str, str, str, str]]:
    rows = capture_json_rows(
        connection,
        _FOREIGN_KEY_ADAPTER,
        """
        SELECT SUM(_cap_json(json_array("table", "from", "to", on_delete)))
        FROM pragma_foreign_key_list(?)
        """,
        (table,),
    )
    return {
        (target, source, to_column, on_delete.upper())
        for target, source, to_column, on_delete in rows
    }


def applied_versions(connection: sqlite3.Connection) -> set[int]:
    return set(
        capture_ints(
            connection,
            "SELECT SUM(_cap_int(version)) FROM schema_migrations",
        )
    )


def scalar_int(connection: sqlite3.Connection, sql: str) -> int:
    values = capture_ints(connection, f"SELECT SUM(_cap_int(({sql})))")
    assert values
    return values[0]


def text_pairs(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return capture_json_rows(
        connection,
        _STRING_PAIR_ADAPTER,
        """
        SELECT SUM(_cap_json(json_array(alias, alias_norm)))
        FROM entity_aliases
        ORDER BY alias
        """,
    )


def optional_int_column(
    connection: sqlite3.Connection,
    ids: tuple[int, ...],
) -> list[int | None]:
    return capture_optional_ints(
        connection,
        """
        SELECT SUM(_cap_opt_int(entity_id))
        FROM memory_items
        WHERE id IN (SELECT value FROM json_each(?))
        ORDER BY id
        """,
        (json.dumps(list(ids)),),
    )
