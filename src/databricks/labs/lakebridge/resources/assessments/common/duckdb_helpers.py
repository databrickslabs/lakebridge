"""Shared DuckDB I/O helpers for profiler extracts.

These live one level above the per-backend folders (``synapse/``, ``mssql/``, …)
because every profiler writes to the same DuckDB sink. Per-backend table
schemas live in ``<backend>/common/schemas.py``; this module is concerned only
with the mechanics of getting a DataFrame into a DuckDB table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pyarrow as pa

logger = logging.getLogger(__name__)

SaveMode = Literal["overwrite", "append"]
Frame = pd.DataFrame | pa.Table

_PROFILER_DUCKDB_CONFIG: dict[str, bool | float | int | list[str] | str] = {
    "storage_compatibility_version": "v1.4.0",
    "force_compression": "auto",
    "zstd_min_string_length": 8,
}


def connect_to_profiler_db(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Connect to a profiler database with native compression enabled."""
    return duckdb.connect(str(db_path), config=_PROFILER_DUCKDB_CONFIG)


def _is_empty(df: Frame) -> bool:
    match df:
        case pd.DataFrame():
            return df.empty
        case pa.Table():
            return df.num_rows == 0
        case _:
            raise TypeError(f"Unsupported frame type: {type(df).__name__}")


def _num_columns(df: Frame) -> int:
    match df:
        case pd.DataFrame():
            return len(df.columns)
        case pa.Table():
            return df.num_columns
        case _:
            raise TypeError(f"Unsupported frame type: {type(df).__name__}")


def save_to_duckdb_conn(
    conn: duckdb.DuckDBPyConnection,
    df: Frame,
    table_name: str,
    mode: SaveMode = "overwrite",
    schema: str | None = None,
) -> None:
    """Write one frame into DuckDB using an already-open connection.

    Both writers reach the target table in more than one statement (``TRUNCATE`` then
    ``INSERT``, or ``DROP``/``CREATE`` then ``INSERT``), so the write runs in a transaction:
    a failure part-way leaves the table as it was rather than truncated or missing. A
    streamed write is one transaction per batch.
    """
    conn.begin()
    try:
        if mode == "overwrite":
            _save_overwrite(conn, df, table_name, schema)
        elif mode == "append":
            _save_append(conn, df, table_name, schema)
        else:
            raise ValueError(f"Unsupported mode '{mode}'. Must be 'overwrite' or 'append'.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.debug("Wrote %d rows to '%s' (mode=%s).", len(df), table_name, mode)


def save_to_duckdb(
    df: Frame,
    table_name: str,
    db_path: str,
    mode: SaveMode = "overwrite",
    schema: str | None = None,
) -> None:
    """Write a frame into a DuckDB table.

    Opens a connection and delegates to :func:`save_to_duckdb_conn`.

    Args:
        df: The data to write.
        table_name: Target table name.
        db_path: Path to the DuckDB database file.
        mode: ``"overwrite"`` (default) or ``"append"``.
        schema: Optional DuckDB schema string (e.g. ``"ID BIGINT, NAME STRING"``).
            When provided, the table is created with this schema and the
            DataFrame's dtypes are ignored. Use this for tables that are
            appended to across runs to avoid drift, for example with dataframes
            where pandas' dtype inference may drift between results (e.g. a column
            that is null-only in one batch but typed in the next).

    Raises:
        ValueError: if ``mode`` is not one of ``"overwrite"`` / ``"append"``.
            The :data:`SaveMode` type narrows this at the call site, but the
            runtime check is kept for callers reaching in from untyped config.
        Any underlying DuckDB error is logged and re-raised.
    """
    try:
        with connect_to_profiler_db(db_path) as conn:
            save_to_duckdb_conn(conn, df, table_name, mode, schema)
    except Exception as e:
        logger.error("Error in save_to_duckdb for table '%s': %s", table_name, str(e))
        raise


def _save_overwrite(
    conn: duckdb.DuckDBPyConnection,
    df: Frame,
    table_name: str,
    schema: str | None,
) -> None:
    """Replace the contents of ``table_name`` with ``df``.

    - ``schema`` provided: ``DROP`` + ``CREATE TABLE (...schema...)`` + ``INSERT``
      (the ``INSERT`` is skipped when ``df`` is empty).
    - ``schema`` omitted, table exists: ``TRUNCATE`` + ``INSERT``. This
      preserves any DDL-declared column types from a prior run.
    - ``schema`` omitted, table missing: ``CREATE TABLE AS SELECT *`` from
      ``df`` (with ``LIMIT 0`` when ``df`` is empty so the columns still land).
    - ``schema`` omitted, table missing, and ``df`` has no columns: warn and
      skip. There is nothing we can do without either a schema or column
      metadata from the DataFrame.
    """
    table_exists = _table_exists(conn, table_name)

    if schema is not None:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.execute(f"CREATE TABLE {table_name} ({schema})")
        if not _is_empty(df):
            conn.register("_lakebridge_df", df)
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM _lakebridge_df")
        return

    if _num_columns(df) == 0:
        if table_exists:
            conn.execute(f"TRUNCATE {table_name}")
        else:
            logger.warning(
                "Cannot create table '%s': empty DataFrame with no columns and no schema provided.",
                table_name,
            )
        return

    conn.register("_lakebridge_df", df)

    if table_exists:
        conn.execute(f"TRUNCATE {table_name}")
        if not _is_empty(df):
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM _lakebridge_df")
        return

    limit_clause = " LIMIT 0" if _is_empty(df) else ""
    conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _lakebridge_df{limit_clause}")


def _save_append(
    conn: duckdb.DuckDBPyConnection,
    df: Frame,
    table_name: str,
    schema: str | None,
) -> None:
    """Append ``df`` to ``table_name``.

    - Empty ``df``: no-op (we do not want a first-batch CTAS firing on what
      may be a transient empty pull from an incremental source).
    - Table missing, ``schema`` provided: ``CREATE TABLE (...schema...)`` +
      ``INSERT``. This is the robust path for incremental appends.
    - Table missing, no ``schema``: ``CREATE TABLE AS SELECT *`` from the
      first batch. Brittle when later batches have different inferred
      dtypes (e.g. a null-only column in batch 1, typed in batch 2). Prefer
      passing a ``schema`` for incremental appends.
    - Table exists: positional ``INSERT``. The DataFrame's column order
      must match the existing table.
    """
    if _is_empty(df):
        logger.info("No rows to append for table '%s'. Skipping.", table_name)
        return

    table_exists = _table_exists(conn, table_name)
    conn.register("_lakebridge_df", df)

    if not table_exists:
        if schema is not None:
            conn.execute(f"CREATE TABLE {table_name} ({schema})")
            conn.execute(f"INSERT INTO {table_name} SELECT * FROM _lakebridge_df")
        else:
            conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _lakebridge_df")
        return

    # Positional insert. Using SELECT * (not BY NAME) sidesteps the
    # column-case mismatch between uppercase queries and whatever
    # casing pandas/SQLAlchemy preserve.
    conn.execute(f"INSERT INTO {table_name} SELECT * FROM _lakebridge_df")


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    result = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()
    return result[0] > 0 if result else False


def get_max_column_value_duckdb(
    column_name: str,
    table_name: str,
    db_path: str,
):
    """Return the maximum value of ``column_name`` in ``table_name``, or ``None``.

    Used by activity extracts to watermark incremental pulls. Returns ``None``
    when the table does not exist (first run) or when an error is encountered.
    """
    max_column_val = None
    try:
        with connect_to_profiler_db(db_path) as conn:
            table_exists = table_name in conn.execute("SHOW TABLES").fetchdf()['name'].values
            if not table_exists:
                logger.info(f"Table {table_name} does not exist in DuckDB. Returning None.")
                return None
            max_column_query = f"SELECT MAX({column_name}) AS last_{column_name} FROM {table_name}"
            logger.info(f"get_max_column_value_duckdb:: query {max_column_query}")
            rows = conn.execute(max_column_query).fetchall()
            max_column_val = rows[0][0] if rows else None
    except Exception as e:
        logger.error(f"ERROR: {e}")
    logger.info(f"max_column_val = {max_column_val}")
    return max_column_val
