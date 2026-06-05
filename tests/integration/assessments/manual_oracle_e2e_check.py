"""
Manual end-to-end fidelity check for the Oracle profiler DuckDB -> Delta ingest path.

This is NOT a pytest test (the ``manual_`` prefix keeps pytest from collecting it). It requires a real
Databricks runtime (it uses ``databricks-connect``) and a real Oracle ``profiler_extract.db``, so it is
run by hand rather than in CI. It exercises the *shipped* ingest logic (``build_spark_schema`` +
``spark.createDataFrame``) against every table in the extract and asserts:

  * row counts match between DuckDB and Spark,
  * DuckDB ``TIMESTAMP`` columns land as Spark ``TIMESTAMP_NTZ`` with wall-clock values unchanged,
  * numeric (DOUBLE/BIGINT/INTEGER) column values round-trip value-exact.

Usage (from the project root, using the project venv so the package + databricks-connect are importable):

    .venv/bin/python tests/integration/assessments/manual_oracle_e2e_check.py \
        --profile <your-databricks-cli-profile> \
        --extract /path/to/profiler_extract.db

Exit code is 0 on full fidelity, 1 otherwise.
"""

import argparse
import sys
from pathlib import Path

import duckdb
from pyspark.sql.types import DoubleType, FloatType, LongType, IntegerType, TimestampNTZType

from databricks.connect import DatabricksSession
from databricks.labs.lakebridge.assessments.dashboards.execute import build_spark_schema

# Column types whose values we compare for exact round-trip fidelity.
_NUMERIC_TYPES = (DoubleType, FloatType, LongType, IntegerType)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="Databricks CLI profile to connect with.")
    parser.add_argument(
        "--extract",
        required=True,
        help="Path to the Oracle profiler_extract.db DuckDB file.",
    )
    parser.add_argument(
        "--no-serverless",
        action="store_true",
        help="Use the profile's configured cluster instead of serverless compute.",
    )
    return parser.parse_args()


def _spark_session(profile: str, serverless: bool) -> DatabricksSession:
    builder = DatabricksSession.builder.profile(profile)
    if serverless:
        builder = builder.serverless(True)
    return builder.getOrCreate()


def _sorted_non_null(values: list) -> list:
    return sorted(v for v in values if v is not None)


def _check_table(spark, con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Ingest one table via the shipped path and compare it to the DuckDB source. Returns True if faithful."""
    relation = con.sql(f"SELECT * FROM {table}")
    df = spark.createDataFrame(relation.df(), schema=build_spark_schema(relation.columns, relation.types))

    duck_rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    spark_rows = df.count()
    rows_ok = duck_rows == spark_rows

    ntz_cols = [f.name for f in df.schema.fields if isinstance(f.dataType, TimestampNTZType)]
    numeric_cols = [f.name for f in df.schema.fields if isinstance(f.dataType, _NUMERIC_TYPES)]

    value_mismatches = []
    for col in ntz_cols + numeric_cols:
        duck_vals = _sorted_non_null([r[0] for r in con.execute(f"SELECT {col} FROM {table}").fetchall()])
        spark_vals = _sorted_non_null([r[col] for r in df.select(col).collect()])
        exact = len(duck_vals) == len(spark_vals) and all(repr(a) == repr(b) for a, b in zip(duck_vals, spark_vals))
        if not exact:
            value_mismatches.append(col)

    is_faithful = rows_ok and not value_mismatches
    status = "OK" if is_faithful else "MISMATCH"
    print(f"{table:30s} rows {duck_rows}=={spark_rows} {status}  ntz={ntz_cols}")
    if not rows_ok:
        print(f"      row count mismatch: duck={duck_rows} spark={spark_rows}")
    for col in value_mismatches:
        print(f"      value mismatch in column: {col}")
    return is_faithful


def main() -> int:
    args = _parse_args()
    extract_path = Path(args.extract).expanduser()
    if not extract_path.exists():
        print(f"Extract not found: {extract_path}", file=sys.stderr)
        return 1

    spark = _spark_session(args.profile, serverless=not args.no_serverless)
    all_ok = True
    with duckdb.connect(database=str(extract_path), read_only=True) as con:
        tables = [row[2] for row in con.execute("SHOW ALL TABLES").fetchall()]
        for table in tables:
            all_ok &= _check_table(spark, con, table)

    print(f"\nE2E fidelity: {'PASS' if all_ok else 'FAIL'} ({len(tables)} tables)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
