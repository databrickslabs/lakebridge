"""Shared helpers for the profiler extract correctness tests.

Kept in a non-``test_`` module so pytest does not collect it and so the schema
parsing / DuckDB inspection helpers live in one place rather than being
duplicated across the per-profiler test modules.
"""

from __future__ import annotations

from collections.abc import Sequence

import duckdb

from tests.integration.debug_envgetter import TestEnvGetter

# Maps the type tokens used in the pinned profiler schemas (e.g. SYNAPSE_SCHEMAS)
# to the type name DuckDB reports back via information_schema -- a declared STRING
# column is a DuckDB VARCHAR, etc.
DECLARED_TO_DUCKDB_TYPE = {
    "STRING": "VARCHAR",
    "BIGINT": "BIGINT",
    "INTEGER": "INTEGER",
    "DOUBLE": "DOUBLE",
    "BOOLEAN": "BOOLEAN",
}


def env_available(keys: Sequence[str]) -> bool:
    """Return True only if every ``key`` resolves via the test env getter.

    Used to skip integration tests when the sandbox credentials are absent
    (``TestEnvGetter.get`` raises ``KeyError`` for a missing key).
    """
    env = TestEnvGetter(True)
    try:
        return all(env.get(key) for key in keys)
    except KeyError:
        return False


def parse_declared_schema(schema: str) -> list[tuple[str, str]]:
    """Parse a ``"NAME TYPE, NAME TYPE, ..."`` schema string into (name, duckdb_type) pairs.

    Order is preserved so callers can also assert column count / ordering.
    """
    columns: list[tuple[str, str]] = []
    for part in schema.split(","):
        name, _, type_token = part.strip().partition(" ")
        columns.append((name.lower(), DECLARED_TO_DUCKDB_TYPE[type_token.strip().upper()]))
    return columns


def actual_schema(db_path: str, table: str) -> list[tuple[str, str]]:
    """Return the ``(column_name, data_type)`` pairs of ``table`` in declaration order."""
    with duckdb.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    return [(name.lower(), data_type) for name, data_type in rows]
