from pathlib import Path

import duckdb
import sqlglot
import yaml
from sqlglot import exp

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/snowflake/pipeline_config.yml"


def test_snowflake_queries_match_duckdb_ddl() -> None:
    config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))

    for step in config["steps"]:
        if step["type"] != "sql":
            continue

        query_path = _REPO_ROOT / step["extract_source"]
        ddl_path = _REPO_ROOT / step["ddl_source"]
        query = sqlglot.parse_one(query_path.read_text(encoding="utf-8"), read="snowflake")
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
