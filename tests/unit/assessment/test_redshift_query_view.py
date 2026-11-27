"""The Redshift `query_view` classifies statements as Redshift records them in `sys_query_history`.

The view runs unchanged on DuckDB over a stand-in `sys_query_history`. Each case is a statement's
`query_text` and `query_type` as a Redshift cluster recorded them: a line break in the text is a
backslash and `n`. COPY and UNLOAD use the types AWS documents.
"""

from pathlib import Path

import duckdb
import pytest

_QUERY_VIEW_SQL = (
    Path(__file__).resolve().parents[3]
    / "src/databricks/labs/lakebridge/resources/assessments/redshift/sql/0_query_view.sql"
)

CASES = [
    ("select count(*) from src", "SELECT", "BI Query"),
    ("with x as (select id from src) select count(*) from x", "SELECT", "BI Query"),
    ("select * from stv_recents", "SELECT", "System"),
    ("create table ctas_plain as select * from src", "CTAS", "Transform"),
    ("CREATE TABLE ctas_ml\\nAS\\nSELECT * FROM src", "CTAS", "Transform"),
    ("select * into sel_into from src", "CTAS", "Transform"),
    ("/* job */ select * into sel_into_c from src", "CTAS", "Transform"),
    ("refresh materialized view mv", "CTAS", "Transform"),
    ("insert into tgt select * from src", "INSERT", "Transform"),
    ("update tgt set v = 'u' where id = 1", "UPDATE", "Transform"),
    ("delete from tgt where id = 2", "DELETE", "Transform"),
    ("merge into tgt using src on tgt.id = src.id remove duplicates", "OTHER", "Transform"),
    ("-- nightly job\\nmerge into tgt using src on tgt.id = src.id remove duplicates", "OTHER", "Transform"),
    ("call p_load()", "UTILITY", "Transform"),
    ("/* job */ refresh materialized view mv", "UTILITY", "Transform"),
    ("copy t from 's3://bucket/key'", "COPY", "Ingestion"),
    ("unload ('select 1') to 's3://bucket/key'", "UNLOAD", "Extract and Load"),
    ("create table t (id int)", "DDL", "Unknown"),
]


@pytest.fixture(name="query_types", scope="module")
def fixture_query_types() -> dict[str, str]:
    with duckdb.connect() as conn:
        conn.execute(
            "CREATE TABLE sys_query_history (user_id INTEGER, query_id INTEGER, query_text VARCHAR, "
            "query_type VARCHAR, start_time TIMESTAMP, end_time TIMESTAMP, query_label VARCHAR)"
        )
        rows = [(1, i, text, type_, "2026-01-01", "2026-01-01", "default") for i, (text, type_, _) in enumerate(CASES)]
        conn.executemany("INSERT INTO sys_query_history VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute(_QUERY_VIEW_SQL.read_text(encoding="utf-8"))
        return dict(conn.execute("SELECT query_text, query_type FROM query_view").fetchall())


@pytest.mark.parametrize(("query_text", "recorded_type", "expected"), CASES)
def test_query_view_classifies_statement(
    query_types: dict[str, str], query_text: str, recorded_type: str, expected: str
) -> None:
    assert query_types[query_text] == expected, recorded_type
