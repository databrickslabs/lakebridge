"""Check the Redshift `query_view` workload classification against sample statements.

The view's SQL runs unchanged on DuckDB, with a `sys_query_history` stand-in table: the CASE uses only
LIKE, lower, ltrim, translate and chr, which behave the same on both engines.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

_QUERY_VIEW_SQL = (
    Path(__file__).resolve().parents[3]
    / "src/databricks/labs/lakebridge/resources/assessments/redshift/sql/0_query_view.sql"
)

CASES = [
    ("create table t as select 1", "Transform"),
    ("CREATE TABLE t\nAS\nSELECT 1", "Transform"),
    ("create temp table t as select 1", "Transform"),
    ("select a into new_t from s", "Transform"),
    ("merge into t using s on t.id = s.id remove duplicates", "Transform"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = s.a", "Transform"),
    ("refresh materialized view mv", "Transform"),
    ("call etl.refresh_daily()", "Transform"),
    ("insert into t select * from s", "Transform"),
    ("copy t from 's3://bucket/key'", "Ingestion"),
    ("select * from t", "BI Query"),
    ("with x as (select 1) select * from x", "BI Query"),
    ("vacuum t", "Unknown"),
]


@pytest.fixture(name="query_types")
def fixture_query_types() -> dict[str, str]:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE sys_query_history (
            user_id INTEGER, query_id INTEGER, query_text VARCHAR,
            start_time TIMESTAMP, end_time TIMESTAMP, query_label VARCHAR
        )
        """)
    rows = [(1, i, text, "2026-01-01", "2026-01-01", "default") for i, (text, _) in enumerate(CASES)]
    rows.append((1, len(CASES), "select 1", "2026-01-01", "2026-01-01", "metrics"))
    conn.executemany("INSERT INTO sys_query_history VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.execute(_QUERY_VIEW_SQL.read_text(encoding="utf-8"))
    return dict(conn.execute("SELECT query_text, query_type FROM query_view").fetchall())


@pytest.mark.parametrize(("query_text", "expected"), CASES)
def test_query_view_classifies_statement(query_types: dict[str, str], query_text: str, expected: str) -> None:
    assert query_types[query_text] == expected


def test_query_view_excludes_system_labels(query_types: dict[str, str]) -> None:
    assert len(query_types) == len(CASES)
