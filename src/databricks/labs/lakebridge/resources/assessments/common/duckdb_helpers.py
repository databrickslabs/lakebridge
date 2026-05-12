"""Shared DuckDB I/O helpers for profiler extracts.

These live one level above the per-backend folders (``synapse/``, ``mssql/``, …)
because every profiler writes to the same DuckDB sink. Per-backend table
schemas live in ``<backend>/common/schemas.py``; this module is concerned only
with the mechanics of getting a DataFrame into a DuckDB table.
"""

from __future__ import annotations

import logging

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


def save_to_duckdb(
    df: pd.DataFrame,
    table_name: str,
    db_path: str,
    mode: str = "overwrite",
    schema: str | None = None,
) -> None:
    """Write a DataFrame into a DuckDB table.

    Args:
        df: The data to write.
        table_name: Target table name.
        db_path: Path to the DuckDB database file.
        mode: ``"overwrite"`` (default) or ``"append"``.
        schema: Optional DuckDB schema string (e.g. ``"ID BIGINT, NAME STRING"``).
            When provided, the table is created with this schema and the
            DataFrame's dtypes are ignored. Use this for tables that are
            appended to across runs, where pandas' dtype inference can drift
            between batches (e.g. a column that is null-only in one batch but
            typed in the next).

    Behavior:
        - ``append`` with an empty DataFrame is a no-op.
        - ``overwrite`` with an explicit ``schema``: drop, recreate, insert.
        - ``overwrite`` without a schema, table exists: truncate and insert
          (preserves any DDL-declared schema on the existing table).
        - ``overwrite`` without a schema, table missing: ``CREATE TABLE AS
          SELECT * FROM df`` (an empty df with columns yields an empty table).
        - ``append`` without a schema, table missing: ``CREATE TABLE AS
          SELECT *`` from the first batch (brittle for incremental appends —
          prefer passing a ``schema`` for that case).

    Raises:
        ValueError: if ``mode`` is not one of ``"overwrite"`` / ``"append"``.
        Any underlying DuckDB error is logged and re-raised.
    """
    if mode not in ("overwrite", "append"):
        raise ValueError(f"Unsupported mode '{mode}'. Must be 'overwrite' or 'append'.")

    try:
        with duckdb.connect(db_path) as conn:
            table_exists = _table_exists(conn, table_name)

            if mode == "append" and df.empty:
                logger.info("No rows to append for table '%s'. Skipping.", table_name)
                return

            if not table_exists and schema is None and len(df.columns) == 0:
                logger.warning(
                    "Cannot create table '%s': empty DataFrame with no columns and no schema provided.",
                    table_name,
                )
                return

            conn.register("_lakebridge_df", df)
            needs_insert = False

            if mode == "overwrite":
                if schema is not None:
                    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                    conn.execute(f"CREATE TABLE {table_name} ({schema})")
                    needs_insert = not df.empty
                elif table_exists:
                    conn.execute(f"TRUNCATE {table_name}")
                    needs_insert = not df.empty
                else:
                    limit_clause = " LIMIT 0" if df.empty else ""
                    conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _lakebridge_df{limit_clause}")
            else:  # append
                if not table_exists:
                    if schema is not None:
                        conn.execute(f"CREATE TABLE {table_name} ({schema})")
                        needs_insert = True
                    else:
                        # First batch defines the schema. Brittle for incremental appends.
                        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM _lakebridge_df")
                else:
                    needs_insert = True

            # Positional insert. Using SELECT * (not BY NAME) sidesteps the
            # column-case mismatch between uppercase queries and whatever
            # casing pandas/SQLAlchemy preserve.
            if needs_insert:
                conn.execute(f"INSERT INTO {table_name} SELECT * FROM _lakebridge_df")

            logger.info("Wrote %d rows to '%s' (mode=%s).", len(df), table_name, mode)
    except Exception as e:
        logger.error("Error in save_to_duckdb for table '%s': %s", table_name, str(e))
        raise


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
        with duckdb.connect(db_path) as conn:
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
