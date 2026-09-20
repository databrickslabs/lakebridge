"""Unit tests for generic Profiler metadata persistence (profiler_metadata)."""

import duckdb

from databricks.labs.lakebridge.assessments.profiler import Profiler


def _tables(db_path):
    with duckdb.connect(str(db_path)) as conn:
        return [r[0] for r in conn.execute("SHOW TABLES").fetchall()]


def test_write_metadata_one_column_per_key(tmp_path):
    db_path = tmp_path / "p.duckdb"
    Profiler._write_metadata({"region": "us-west-2"}, db_path)
    with duckdb.connect(str(db_path)) as conn:
        cols = [c[0] for c in conn.execute("DESCRIBE profiler_metadata").fetchall()]
        rows = conn.execute("SELECT region FROM profiler_metadata").fetchall()
    assert cols == ["region"]
    assert rows == [("us-west-2",)]


def test_write_metadata_multiple_keys_become_columns(tmp_path):
    db_path = tmp_path / "p.duckdb"
    Profiler._write_metadata({"region": "eu-west-1", "cloud": "aws"}, db_path)
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT region, cloud FROM profiler_metadata").fetchall()
    assert rows == [("eu-west-1", "aws")]


def test_write_metadata_empty_is_noop(tmp_path):
    db_path = tmp_path / "p.duckdb"
    Profiler._write_metadata({}, db_path)
    assert "profiler_metadata" not in _tables(db_path)


def test_write_metadata_skips_non_identifier_keys(tmp_path):
    db_path = tmp_path / "p.duckdb"
    # Keys become column names, so non-identifier keys are dropped defensively.
    Profiler._write_metadata({"bad key": "x", "drop table;": "y"}, db_path)
    assert "profiler_metadata" not in _tables(db_path)
