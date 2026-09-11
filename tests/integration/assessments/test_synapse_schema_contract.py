"""Schema-contract correctness tests for the Synapse profiler extracts.

Unlike the SQL profilers, the Synapse profiler extracts run as Python steps that
hit Azure Synapse REST APIs and SQL pools, so a full end-to-end run cannot be
driven from the test sandbox. What *can* be validated deterministically is the
ingestion contract every Synapse extract relies on: each table is written with
``save_to_duckdb(df, table, schema=SYNAPSE_SCHEMAS[table])``.

These tests assert that every declared schema in ``SYNAPSE_SCHEMAS``:

* is valid DuckDB DDL (``save_to_duckdb`` creates the table without error), and
* produces a table whose columns and types exactly match the declaration.

This catches typos, invalid types, duplicate columns, and drift in the pinned
schemas -- the failure modes those declarations exist to prevent -- without
needing a live Synapse workspace.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.schemas import SYNAPSE_SCHEMAS

from tests.integration.assessments.profiler_extract_helpers import (
    DECLARED_TO_DUCKDB_TYPE,
    actual_schema,
    parse_declared_schema,
)


def test_synapse_schemas_is_not_empty() -> None:
    # Guards against the parametrized tests silently passing on an empty mapping.
    assert SYNAPSE_SCHEMAS


@pytest.mark.parametrize("table_name", sorted(SYNAPSE_SCHEMAS))
def test_synapse_schema_declares_only_known_types(table_name: str) -> None:
    """Every declared column uses a type token this contract knows how to map."""
    for part in SYNAPSE_SCHEMAS[table_name].split(","):
        _, _, type_token = part.strip().partition(" ")
        assert (
            type_token.strip().upper() in DECLARED_TO_DUCKDB_TYPE
        ), f"Table '{table_name}' declares unknown type token '{type_token.strip()}'"


@pytest.mark.parametrize("table_name", sorted(SYNAPSE_SCHEMAS))
def test_synapse_schema_round_trips_through_save_to_duckdb(tmp_path: Path, table_name: str) -> None:
    """Each declared schema is valid DDL and yields exactly the declared columns and types."""
    db_path = str(tmp_path / "synapse_extract.duckdb")
    expected = parse_declared_schema(SYNAPSE_SCHEMAS[table_name])

    # A row-less frame with the declared columns exercises the schema-driven
    # CREATE TABLE path without needing representative rows: the INSERT is skipped
    # for an empty frame, so save_to_duckdb owns the column types via the schema.
    # (An entirely column-less DataFrame cannot be registered with DuckDB.)
    empty_frame = pd.DataFrame(columns=[name for name, _ in expected])
    save_to_duckdb(empty_frame, table_name, db_path, schema=SYNAPSE_SCHEMAS[table_name])

    actual = actual_schema(db_path, table_name)
    assert actual == expected, f"Schema mismatch for Synapse table '{table_name}'"
