"""Assert every profiler `type: sql` step's extract query lines up with its DuckDB `ddl_source`.

One parametrized case per source keeps the check exhaustive without duplicating the assertion
body in a file per source (which trips pylint's duplicate-code / R0801 once two coexist).
"""

from pathlib import Path

import duckdb
import pytest
import sqlglot
import yaml
from sqlglot import exp

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENTS = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"

# Profiler source -> sqlglot dialect its extract queries are written in.
_DDL_SOURCES = {
    "legacy_synapse": "tsql",
    "redshift": "redshift",
    "snowflake": "snowflake",
}


@pytest.mark.parametrize(("source", "dialect"), sorted(_DDL_SOURCES.items()))
def test_profiler_queries_match_duckdb_ddl(source: str, dialect: str) -> None:
    config_path = _ASSESSMENTS / source / "pipeline_config.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    for step in config["steps"]:
        if step["type"] != "sql":
            continue

        query_path = _REPO_ROOT / step["extract_source"]
        ddl_path = _REPO_ROOT / step["ddl_source"]
        query = sqlglot.parse_one(query_path.read_text(encoding="utf-8"), read=dialect)
        assert isinstance(query, exp.Query)
        ddl_text = ddl_path.read_text(encoding="utf-8")
        ddl = sqlglot.parse_one(ddl_text, read="duckdb")
        schema = ddl.find(exp.Schema)
        assert schema is not None

        query_columns = [column.lower() for column in query.named_selects]
        ddl_columns = [column.this.name.lower() for column in schema.expressions]
        assert query_columns == ddl_columns, step["name"]

        with duckdb.connect(":memory:") as conn:
            conn.execute(ddl_text)
