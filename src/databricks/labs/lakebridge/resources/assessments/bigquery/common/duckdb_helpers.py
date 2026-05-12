import logging

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


def insert_df_to_duckdb(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """
    Insert a pandas DataFrame into a DuckDB table, dropping any existing table first.

    Mirrors the Synapse helper of the same name. Empty DataFrames create an empty table
    with the inferred schema (or skip creation entirely if the DataFrame has no columns).
    """
    try:
        with duckdb.connect(db_path) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")

            if df.empty:
                if len(df.columns) > 0:
                    conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df LIMIT 0")
                    logger.info(f"Created empty table {table_name} with schema: {df.columns.tolist()}")
                else:
                    logger.warning(f"Skipping table {table_name} creation as DataFrame has no columns")
                return

            conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
            logger.info(f"Inserted {len(df)} rows into {table_name}")
    except duckdb.Error as exc:
        logger.error(f"Error inserting data into DuckDB table {table_name}: {exc}")
        raise
