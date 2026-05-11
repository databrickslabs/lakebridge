"""Deprecated: shims that delegate to ``assessments.common.duckdb_helpers``.

Retained temporarily so the Synapse and MSSQL extract scripts can be migrated
one file at a time without breaking the build at any intermediate commit.
This module will be deleted once all callers have been migrated.
"""

from __future__ import annotations

import pandas as pd

from databricks.labs.lakebridge.connections.database_manager import FetchResult
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import (
    get_max_column_value_duckdb,
    save_to_duckdb,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.schemas import SYNAPSE_SCHEMAS

__all__ = [
    "save_resultset_to_db",
    "insert_df_to_duckdb",
    "get_max_column_value_duckdb",
]


def save_resultset_to_db(
    result: FetchResult,
    table_name: str,
    db_path: str,
    mode: str,
) -> None:
    """Deprecated. Use ``save_to_duckdb`` with the appropriate per-backend schema map."""
    schema = SYNAPSE_SCHEMAS.get(table_name)
    if schema is None:
        available = list(SYNAPSE_SCHEMAS.keys())
        raise ValueError(
            f"Table '{table_name}' not found in SYNAPSE_SCHEMAS. Available: {available}"
        )
    save_to_duckdb(result.to_df(), table_name, db_path, mode=mode, schema=schema)


def insert_df_to_duckdb(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """Deprecated. Use ``save_to_duckdb`` directly."""
    save_to_duckdb(df, table_name, db_path, mode="overwrite")
