from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import (
    save_to_duckdb,
)


def _read_table(db_path: str, table_name: str) -> pd.DataFrame:
    with duckdb.connect(db_path) as conn:
        return conn.execute(f"SELECT * FROM {table_name}").fetchdf()


def _column_types(db_path: str, table_name: str) -> dict[str, str]:
    with duckdb.connect(db_path) as conn:
        rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    return {row[0]: row[1] for row in rows}


def test_overwrite_creates_table_from_dataframe(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})

    save_to_duckdb(df, "t1", db_path)

    out = _read_table(db_path, "t1").sort_values("id").reset_index(drop=True)
    pd.testing.assert_frame_equal(out, df)


def test_overwrite_replaces_existing_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    save_to_duckdb(pd.DataFrame({"id": [1]}), "t1", db_path)
    save_to_duckdb(pd.DataFrame({"id": [9, 10]}), "t1", db_path)

    out = _read_table(db_path, "t1").sort_values("id").reset_index(drop=True)
    assert out["id"].tolist() == [9, 10]


def test_overwrite_with_explicit_schema_pins_dtypes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    df = pd.DataFrame({"id": [1, 2], "label": ["x", "y"]})

    save_to_duckdb(df, "t1", db_path, schema="id BIGINT, label VARCHAR")

    types = _column_types(db_path, "t1")
    assert types == {"id": "BIGINT", "label": "VARCHAR"}


def test_overwrite_empty_dataframe_with_columns_creates_empty_table(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    df = pd.DataFrame({"id": pd.Series(dtype="int64"), "name": pd.Series(dtype="object")})

    save_to_duckdb(df, "t1", db_path)

    out = _read_table(db_path, "t1")
    assert out.empty
    assert list(out.columns) == ["id", "name"]


def test_append_creates_table_when_missing(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    df = pd.DataFrame({"id": [1, 2]})

    save_to_duckdb(df, "t1", db_path, mode="append")

    out = _read_table(db_path, "t1").sort_values("id").reset_index(drop=True)
    assert out["id"].tolist() == [1, 2]


def test_append_accumulates_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    save_to_duckdb(pd.DataFrame({"id": [1, 2]}), "t1", db_path, mode="append", schema="id BIGINT")
    save_to_duckdb(pd.DataFrame({"id": [3, 4]}), "t1", db_path, mode="append", schema="id BIGINT")

    out = _read_table(db_path, "t1").sort_values("id").reset_index(drop=True)
    assert out["id"].tolist() == [1, 2, 3, 4]


def test_append_empty_dataframe_is_noop(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    save_to_duckdb(pd.DataFrame({"id": [1]}), "t1", db_path, schema="id BIGINT")

    save_to_duckdb(pd.DataFrame({"id": []}), "t1", db_path, mode="append", schema="id BIGINT")

    out = _read_table(db_path, "t1")
    assert out["id"].tolist() == [1]


def test_append_with_explicit_schema_survives_dtype_drift(tmp_path: Path) -> None:
    """Real-world bug guard: column starts null-only in batch 1, has values in batch 2.

    Without an explicit schema the first append creates a NULL-typed column and
    the second insert fails. With an explicit schema, DuckDB owns the type.
    """
    db_path = str(tmp_path / "t.duckdb")
    schema = "id BIGINT, login_time STRING"

    batch_1 = pd.DataFrame({"id": [1, 2], "login_time": [None, None]})
    batch_2 = pd.DataFrame({"id": [3, 4], "login_time": ["2025-01-01", "2025-01-02"]})

    save_to_duckdb(batch_1, "t1", db_path, mode="append", schema=schema)
    save_to_duckdb(batch_2, "t1", db_path, mode="append", schema=schema)

    out = _read_table(db_path, "t1").sort_values("id").reset_index(drop=True)
    assert out["id"].tolist() == [1, 2, 3, 4]
    assert out["login_time"].tolist() == [None, None, "2025-01-01", "2025-01-02"]


def test_overwrite_without_schema_truncates_when_table_exists(tmp_path: Path) -> None:
    """Existing DDL-declared column types should survive an overwrite without ``schema``."""
    db_path = str(tmp_path / "t.duckdb")
    with duckdb.connect(db_path) as conn:
        conn.execute("CREATE TABLE t1 (id BIGINT, label VARCHAR)")
        conn.execute("INSERT INTO t1 VALUES (1, 'old')")

    save_to_duckdb(pd.DataFrame({"id": [2], "label": ["new"]}), "t1", db_path)

    types = _column_types(db_path, "t1")
    assert types == {"id": "BIGINT", "label": "VARCHAR"}
    out = _read_table(db_path, "t1")
    assert out["label"].tolist() == ["new"]


def test_invalid_mode_raises(tmp_path: Path) -> None:
    db_path = str(tmp_path / "t.duckdb")
    with pytest.raises(ValueError, match="Unsupported mode"):
        # Intentionally violating the Literal type to exercise the runtime guard
        # that protects callers reaching in from untyped config / JSON.
        save_to_duckdb(pd.DataFrame({"id": [1]}), "t1", db_path, mode="upsert")  # type: ignore[arg-type]
