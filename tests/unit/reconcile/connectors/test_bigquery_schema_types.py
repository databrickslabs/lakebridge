"""Guardrail for BigQuery schema-reconcile type handling.

Schema reconciliation runs in two stages: `BigQueryDataSource.get_schema()` emits a canonical type
string (Stage 1), then `schema_compare._validate_parsed_query` round-trips it through sqlglot against the
Databricks target (Stage 2). This test replicates the Stage-2 round-trip for every BigQuery type and
asserts that, after the connector's Stage-1 canonicalization, it validates against the empirically-tested
Databricks target (FE GCP + DBSQL 2026.10). It fails loudly if a sqlglot upgrade or a connector change
breaks a mapping, so "which types need Stage-1 handling" stays a repeatable check rather than tribal
knowledge.

`STAGE1` must mirror the `_SCHEMA_QUERY` CASE in `connectors/bigquery.py`.
"""

import pytest
from sqlglot import parse_one
from sqlglot import expressions as exp

from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import (
    bigquery_decimal_transform,
    transform_expression,
)
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

_BQ = get_dialect("bigquery")
_DBX = get_dialect("databricks")

# (bq_type as reported by INFORMATION_SCHEMA, stage-1 emit string, tested Databricks target).
# stage-1 emit == bq_type means "leave raw" (sqlglot translates it correctly); otherwise it must
# mirror the `_SCHEMA_QUERY` CASE in connectors/bigquery.py. `needs_stage1` marks the types that
# would NOT reconcile if left raw, which justifies the CASE entry.
TYPE_CASES = [
    # bq_type, stage1_emit, target, needs_stage1
    ("INT64", "INT64", "BIGINT", False),
    ("FLOAT64", "FLOAT64", "DOUBLE", False),
    ("BOOL", "BOOL", "BOOLEAN", False),
    ("STRING", "STRING", "STRING", False),
    ("BYTES", "BYTES", "BINARY", False),
    ("DATE", "DATE", "DATE", False),
    ("DATETIME", "DATETIME", "TIMESTAMP_NTZ", False),
    ("TIMESTAMP", "TIMESTAMP", "TIMESTAMP", False),
    ("NUMERIC(10, 2)", "NUMERIC(10, 2)", "DECIMAL(10, 2)", False),
    ("GEOGRAPHY", "GEOGRAPHY", "STRING", False),
    ("ARRAY<INT64>", "ARRAY<INT64>", "ARRAY<BIGINT>", False),
    ("STRUCT<a INT64>", "STRUCT<a INT64>", "STRUCT<a: BIGINT>", False),
    # NO-NATIVE / sqlglot-mishandled types — canonicalized in Stage 1
    ("NUMERIC", "decimal(38,9)", "DECIMAL(38,9)", True),
    ("BIGNUMERIC", "string", "STRING", True),
    ("TIME", "string", "STRING", True),
    ("JSON", "variant", "VARIANT", True),
    ("RANGE<DATE>", "struct<start date, end date>", "STRUCT<start: DATE, end: DATE>", True),
    (
        "RANGE<DATETIME>",
        "struct<start timestamp_ntz, end timestamp_ntz>",
        "STRUCT<start: TIMESTAMP_NTZ, end: TIMESTAMP_NTZ>",
        True,
    ),
    ("RANGE<TIMESTAMP>", "struct<start timestamp, end timestamp>", "STRUCT<start: TIMESTAMP, end: TIMESTAMP>", True),
]


def _schema_compare_valid(source_datatype: str, databricks_datatype: str) -> bool:
    """Mirror of schema_compare.SchemaCompare._validate_parsed_query (bidirectional, OR-of-checks)."""

    def _parse(read, write, query: str) -> str:
        return parse_one(query, read=read).sql(dialect=write).replace(", ", ",")

    source_query = f"create table dummy (col {source_datatype})"
    databricks_query = f"create table dummy (col {databricks_datatype})".replace(", ", ",")
    converted_source = _parse(_BQ, _DBX, source_query)
    converted_databricks = _parse(_DBX, _BQ, databricks_query)
    parsed_source_check = converted_source.lower() == databricks_query.lower()
    parsed_databricks_check = source_query.replace(", ", ",").lower() == converted_databricks.lower()
    return parsed_source_check or parsed_databricks_check


@pytest.mark.parametrize("bq_type, stage1_emit, target", [(c[0], c[1], c[2]) for c in TYPE_CASES])
def test_stage1_output_validates_against_tested_target(bq_type, stage1_emit, target):
    assert _schema_compare_valid(
        stage1_emit, target
    ), f"{bq_type}: Stage-1 emit {stage1_emit!r} does not reconcile to target {target!r}"


@pytest.mark.parametrize("bq_type, target", [(c[0], c[2]) for c in TYPE_CASES if c[3]])
def test_raw_type_fails_without_stage1(bq_type, target):
    assert not _schema_compare_valid(
        bq_type, target
    ), f"{bq_type} now reconciles raw to {target!r}; the Stage-1 CASE may be unnecessary — re-check."


@pytest.mark.parametrize(
    "datatype, expected_format",
    [
        ("decimal(10,2)", "%.2f"),  # parameterized scale drives FORMAT precision
        ("decimal(38,9)", "%.9f"),
        ("decimal", "%.9f"),  # no params -> default scale 9
        ("not-a-valid-type", "%.9f"),  # unparseable -> default scale 9 (except branch)
    ],
)
def test_bigquery_decimal_transform_scale(datatype, expected_format):
    rendered = transform_expression(exp.column("c"), bigquery_decimal_transform(datatype)).sql(dialect=_BQ)
    assert expected_format in rendered
