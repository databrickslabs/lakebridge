from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.schemas import SYNAPSE_SCHEMAS


SERVERLESS_ROUTINES_COLUMNS = [
    "ROUTINE_SCHEMA",
    "ROUTINE_NAME",
    "ROUTINE_TYPE",
    "CREATED",
    "LAST_ALTERED",
    "ROUTINE_DEFINITION",
    "POOL_NAME",
]


def _schema_column_names(schema: str) -> list[str]:
    return [part.strip().split()[0] for part in schema.split(",")]


def test_serverless_routines_schema_matches_query_columns() -> None:
    schema_cols = _schema_column_names(SYNAPSE_SCHEMAS["serverless_routines"])

    assert schema_cols == SERVERLESS_ROUTINES_COLUMNS


def test_serverless_routines_schema_accepts_query_shaped_dataframe(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    df = pd.DataFrame(
        [
            {
                "ROUTINE_SCHEMA": "dbo",
                "ROUTINE_NAME": "usp_example",
                "ROUTINE_TYPE": "PROCEDURE",
                "CREATED": "2024-01-01",
                "LAST_ALTERED": "2024-06-01",
                "ROUTINE_DEFINITION": "[REDACTED]",
                "POOL_NAME": "testdb",
            }
        ]
    )

    save_to_duckdb(
        df,
        "serverless_routines",
        db_path,
        mode="overwrite",
        schema=SYNAPSE_SCHEMAS["serverless_routines"],
    )

    with duckdb.connect(db_path) as conn:
        out = conn.execute("SELECT * FROM serverless_routines").fetchdf()

    assert len(out) == 1
    assert out.iloc[0]["ROUTINE_NAME"] == "usp_example"
