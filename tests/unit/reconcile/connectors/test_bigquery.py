import re
from unittest.mock import create_autospec

import pytest
from sqlglot import parse_one

from databricks.labs.lakebridge.reconcile.connectors.bigquery import BigQueryDataSource
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.query_builder.hash_query import HashQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def initial_setup():
    engine = get_dialect("bigquery")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_builds_three_part_backtick_quoted_name():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.read_data("project", "dataset", "employee", "select 1 from :tbl", None)

    reader.read_data.assert_called_once_with(
        "select 1 from `project.dataset.employee`",
        "lakebridge_reconcile",
        "materializationDataset",
    )


def test_read_data_substitutes_bigquery_rendered_placeholder():
    # sqlglot's BigQuery generator renders the `:tbl` placeholder as `@tbl`; read_data must handle it.
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.read_data("scratch_ds", "dataset", "employee", "select 1 from @tbl", None)

    reader.read_data.assert_called_once_with(
        "select 1 from `scratch_ds.dataset.employee`",
        "lakebridge_reconcile",
        "materializationDataset",
    )


def test_read_data_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            "Runtime exception occurred while fetching data using "
            "select 1 from `proj.dataset.employee` : Test Exception"
        ),
    ):
        dfds.read_data("proj", "dataset", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(DataSourceRuntimeException, match=re.escape("Runtime exception occurred while fetching schema")):
        dfds.get_schema("proj", "dataset", "supplier")


def test_get_schema_query_canonicalizes_types_within_family():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.get_schema("proj", "dataset", "supplier")

    schema_query = reader.read_data.call_args.args[0]
    assert "`proj.dataset`.INFORMATION_SCHEMA.COLUMNS" in schema_query
    assert "where table_name = 'supplier'" in schema_query
    # Same-family mappings for the types sqlglot can't bridge on its own.
    assert "when data_type = 'NUMERIC' then 'decimal(38, 9)'" in schema_query
    assert "when data_type = 'JSON' then 'variant'" in schema_query
    # Pinned to the exact Databricks type so a narrower target is not accepted as equivalent.
    assert "when data_type = 'INT64' then 'bigint'" in schema_query
    assert "when data_type = 'FLOAT64' then 'double'" in schema_query
    assert "when data_type = 'DATETIME' then 'timestamp_ntz'" in schema_query
    # Nested NUMERIC gets its implied precision/scale, in BigQuery's own spelling.
    assert "NUMERIC(38, 9)" in schema_query
    # BIGNUMERIC has no exact Databricks equivalent, so it passes through rather than truncating.
    assert "BIGNUMERIC" not in schema_query


@pytest.mark.parametrize(
    "narrower_databricks_type, bigquery_type",
    [
        # Rationale for reporting Databricks type names from _SCHEMA_QUERY: a narrower target converts
        # back to the very BigQuery type the column started as, so a schema compare that reads the
        # source side as its BigQuery spelling accepts the lossy migration. int and smallint overflow
        # INT64's range and float overflows FLOAT64's, so these must not be treated as equivalent.
        # Verified against SchemaCompare itself in tests/integration/reconcile/test_schema_compare.py.
        ("int", "INT64"),
        ("smallint", "INT64"),
        ("float", "FLOAT64"),
    ],
)
def test_sqlglot_cannot_tell_a_narrower_target_apart(narrower_databricks_type, bigquery_type):
    converted = parse_one(f"create table t (c {narrower_databricks_type})", read=get_dialect("databricks")).sql(
        dialect=get_dialect("bigquery")
    )

    assert converted == f"CREATE TABLE t (c {bigquery_type})"


@pytest.mark.parametrize(
    "bigquery_type, sqlglot_databricks_output",
    [
        # No Databricks TIME type, so sqlglot silently rewrites it to TIMESTAMP (different semantics).
        ("TIME", "TIMESTAMP"),
        # BIGNUMERIC maps to BIGDECIMAL, which is not a Databricks type.
        ("BIGNUMERIC", "BIGDECIMAL"),
        # RANGE<T> is passed through unchanged; Databricks has no RANGE type.
        ("RANGE<DATE>", "RANGE<DATE>"),
        # Nesting is not bridged either. Nested JSON stays JSON (Databricks uses variant), so unlike
        # top-level JSON — which the schema query maps to variant — array<json> is not canonicalized.
        ("ARRAY<TIME>", "ARRAY<TIMESTAMP>"),
        ("ARRAY<JSON>", "ARRAY<JSON>"),
    ],
)
def test_sqlglot_has_no_native_databricks_equivalent_for_approximate_types(bigquery_type, sqlglot_databricks_output):
    """Rationale for BigQueryDataSource_APPROXIMATE_TYPES: sqlglot's own BigQuery -> Databricks conversion yields a
    non-Databricks or semantically different type, so these columns can be false schema mismatches."""

    converted = parse_one(f"create table t (c {bigquery_type})", read=get_dialect("bigquery")).sql(
        dialect=get_dialect("databricks")
    )
    assert converted == f"CREATE TABLE t (c {sqlglot_databricks_output})"


def test_list_schemas_and_tables():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.list_schemas("proj")
    assert "`proj`.INFORMATION_SCHEMA.SCHEMATA" in reader.read_data.call_args.args[0]

    dfds.list_tables("proj", "dataset")
    assert "`proj.dataset`.INFORMATION_SCHEMA.TABLES" in reader.read_data.call_args.args[0]


def _build_hash_query(data_source, engine, cols):
    schema = []
    for name, dtype in cols:
        norm = data_source.normalize_identifier(name)
        schema.append(Schema(norm.ansi_normalized, dtype, norm.ansi_normalized, norm.source_normalized))
    table_conf = Table(source_name="t", target_name="t", join_columns=["id"])
    return HashQueryBuilder(table_conf, schema, "source", engine, data_source).build_query("data")


def test_hash_query_emits_bigquery_compatible_sql():
    # Regression for the hash path: BigQuery needs a Dialect_hash_algo_mapping entry (else ValueError)
    # and a cast-to-STRING transform (else CONCAT/TRIM fail on non-string columns).
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)

    query = _build_hash_query(data_source, engine, [("id", "int64"), ("amount", "decimal(38,9)"), ("name", "string")])

    # hex-wrapped SHA-256 (matches Databricks sha2(...,256))
    assert "TO_HEX(SHA256(" in query
    # types without a dedicated transform are cast to STRING for concatenation
    assert "CAST(`id` AS STRING)" in query
    assert "CAST(`name` AS STRING)" in query
    # sqlglot renders the :tbl placeholder as @tbl for BigQuery — read_data handles both
    assert "@tbl" in query or ":tbl" in query


def test_hash_query_formats_types_that_cast_differently_to_spark():
    """BigQuery's CAST(... AS STRING) disagrees with Spark for these types, which changes the row hash:
    it strips a decimal's trailing zeros, drops a whole FLOAT64's fractional part, and appends a UTC
    offset to a TIMESTAMP. Verified equal against live BigQuery and Databricks."""
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)

    query = _build_hash_query(
        data_source,
        engine,
        [
            ("id", "bigint"),
            ("amount", "decimal(38, 9)"),
            ("pct", "decimal(10, 2)"),
            ("score", "double"),
            ("ts", "timestamp"),
            ("dt", "timestamp_ntz"),
        ],
    )

    # Scale is read from the column's own declared type, not a fixed default.
    assert "FORMAT('%.9f', `amount`)" in query
    assert "FORMAT('%.2f', `pct`)" in query
    assert "FORMAT('%t', `score`)" in query
    # %E*S emits only the fractional-second digits present, as Spark does; 'UTC' drops the +00 suffix.
    assert "FORMAT_TIMESTAMP('%F %H:%M:%E*S', `ts`, 'UTC')" in query
    assert "FORMAT_DATETIME('%F %H:%M:%E*S', `dt`)" in query
    assert "CAST(`amount` AS STRING)" not in query


def test_list_schemas_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")
    with pytest.raises(
        DataSourceRuntimeException, match=re.escape("Runtime exception occurred while fetching schemas")
    ):
        dfds.list_schemas("proj")


def test_list_tables_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")
    with pytest.raises(DataSourceRuntimeException, match=re.escape("Runtime exception occurred while fetching tables")):
        dfds.list_tables("proj", "dataset")


def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", "`a`")
    assert data_source.normalize_identifier("`b`") == NormalizedIdentifier("`b`", "`b`")
    assert data_source.normalize_identifier("e`f") == NormalizedIdentifier("`e``f`", "`e``f`")
    assert data_source.normalize_identifier("` g h `") == NormalizedIdentifier("` g h `", "` g h `")
