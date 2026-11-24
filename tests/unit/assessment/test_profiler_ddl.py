"""Every profiler `type: sql` step's DuckDB `ddl_source` matches its extract query.

The pipeline inserts a query's rows into the DDL's table by position, so a DDL must create the
step's table with the query's columns, in order. One parametrized case per source, rather than a
file per source, which pylint's duplicate-code check (R0801) rejects once two coexist.
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
    "mssql/multi_db": "tsql",
    "mssql/single_db": "tsql",
    "oracle": "oracle",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "teradata": "teradata",
}

# Steps whose query sqlglot can't resolve to one SELECT, so only their DDL is checked here.
_UNRESOLVABLE = {
    # Each builds its SELECT as dynamic SQL, run per database by a cursor.
    ("mssql/multi_db", "tables"): "dynamic SQL",
    ("mssql/multi_db", "views"): "dynamic SQL",
    ("mssql/multi_db", "columns"): "dynamic SQL",
    ("mssql/multi_db", "indexed_views"): "dynamic SQL",
    ("mssql/multi_db", "routines"): "dynamic SQL",
    ("mssql/multi_db", "db_sizes"): "dynamic SQL",
    ("mssql/multi_db", "table_sizes"): "dynamic SQL",
    # sqlglot doesn't parse ZEROIFNULL over interval arithmetic, nor OREPLACE / OTRANSLATE.
    ("teradata", "td_pdcr_info_agg_extract"): "sqlglot can't parse the Teradata query",
    ("teradata", "td_pdcr_sp_exe_info_agg_extract"): "sqlglot can't parse the Teradata query",
}


def _output_columns(query: exp.Query) -> list[str]:
    """The query's output column names; a `*` over a subquery or CTE is expanded from it."""
    if isinstance(query, exp.SetOperation):
        return _output_columns(query.left)
    ctes = {cte.alias_or_name.lower(): cte.this for cte in query.ctes}
    names = []
    for projection in query.expressions:
        if not projection.is_star:
            names.append(projection.alias_or_name.lower())
            continue
        source = (query.args.get("from_") or query.args["from"]).this
        inner = source.this if isinstance(source, exp.Subquery) else ctes.get(source.name.lower())
        if inner is None:
            raise ValueError(f"can't expand * from {source.sql()}")
        names.extend(_output_columns(inner))
    return names


def _query_columns(query_text: str, dialect: str) -> list[str]:
    statements = [s for s in sqlglot.parse(query_text, read=dialect) if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError(f"expected one SELECT, got {len(statements)} statements")
    return _output_columns(statements[0])


def _sql_steps(source: str) -> list[dict]:
    config = yaml.safe_load((_ASSESSMENTS / source / "pipeline_config.yml").read_text(encoding="utf-8"))
    return [s for s in config["steps"] if s["type"] == "sql" and s.get("flag", "active") == "active"]


@pytest.mark.parametrize(("source", "dialect"), sorted(_DDL_SOURCES.items()))
def test_profiler_queries_match_duckdb_ddl(source: str, dialect: str) -> None:
    for step in _sql_steps(source):
        ddl_text = (_REPO_ROOT / step["ddl_source"]).read_text(encoding="utf-8")
        ddl = sqlglot.parse_one(ddl_text, read="duckdb")
        table, schema = ddl.find(exp.Table), ddl.find(exp.Schema)
        assert table is not None and schema is not None, step["name"]
        assert table.name.lower() == step["name"].lower(), step["name"]
        with duckdb.connect(":memory:") as conn:
            conn.execute(ddl_text)

        ddl_columns = [column.this.name.lower() for column in schema.expressions]
        query_text = (_REPO_ROOT / step["extract_source"]).read_text(encoding="utf-8")
        try:
            query_columns = _query_columns(query_text, dialect)
        except (sqlglot.errors.ParseError, ValueError):
            assert (source, step["name"]) in _UNRESOLVABLE, f"{step['name']}: can't resolve its query's columns"
            continue
        assert query_columns == ddl_columns, step["name"]


def test_every_profiler_with_sql_steps_is_checked() -> None:
    configs = {p.parent.relative_to(_ASSESSMENTS).as_posix() for p in _ASSESSMENTS.rglob("pipeline_config.yml")}
    with_sql_steps = {source for source in configs if _sql_steps(source)}
    assert with_sql_steps == set(_DDL_SOURCES)
