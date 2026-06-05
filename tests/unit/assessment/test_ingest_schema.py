"""
Unit tests for the DuckDB -> Spark schema mapping used when ingesting a profiler extract into Delta.

The key behaviour these lock in: DuckDB's timezone-naive ``TIMESTAMP`` maps to Spark ``TIMESTAMP_NTZ``
(preserving the wall-clock value) rather than the timezone-dependent ``TIMESTAMP`` (LTZ) that Spark's
pandas schema inference would otherwise pick. Unmapped types fall back to inference (return ``None``).
"""

import duckdb
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructType,
    TimestampNTZType,
    TimestampType,
)

from databricks.labs.lakebridge.assessments.dashboards.execute import build_spark_schema


def _schema_from_sql(select_sql: str) -> StructType | None:
    with duckdb.connect() as conn:
        relation = conn.sql(select_sql)
        return build_spark_schema(relation.columns, relation.types)


def test_oracle_scalar_types_map_to_expected_spark_types() -> None:
    schema = _schema_from_sql("""
        SELECT
            CAST('x' AS VARCHAR) AS v,
            CAST(1 AS INTEGER) AS i,
            CAST(1 AS BIGINT) AS b,
            CAST(1.5 AS DOUBLE) AS d,
            CAST('2026-04-03 13:55:26' AS TIMESTAMP) AS t
        """)
    assert schema is not None
    by_name = {f.name: f.dataType for f in schema.fields}
    assert isinstance(by_name["v"], StringType)
    assert isinstance(by_name["i"], IntegerType)
    assert isinstance(by_name["b"], LongType)
    assert isinstance(by_name["d"], DoubleType)
    # The fidelity-critical mapping: naive TIMESTAMP -> TIMESTAMP_NTZ, not LTZ.
    assert isinstance(by_name["t"], TimestampNTZType)


def test_timestamptz_maps_to_ltz() -> None:
    schema = _schema_from_sql("SELECT CAST('2026-04-03 13:55:26+00' AS TIMESTAMP WITH TIME ZONE) AS t")
    assert schema is not None
    assert isinstance(schema.fields[0].dataType, TimestampType)


def test_unmapped_type_falls_back_to_inference() -> None:
    # A nested STRUCT column has no explicit mapping, so the whole table falls back to inference.
    schema = _schema_from_sql("SELECT {'a': 1}::STRUCT(a INTEGER) AS s")
    assert schema is None
