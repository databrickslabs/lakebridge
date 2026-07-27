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
from databricks.labs.lakebridge.reconcile.recon_output_config import SchemaMatchResult
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
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
    "source_datatype, databricks_datatype, is_valid",
    [
        # The schema query reports the Databricks spelling, so a narrower target no longer matches.
        ("bigint", "bigint", True),
        ("bigint", "int", False),
        ("bigint", "smallint", False),
        ("double", "double", True),
        ("double", "float", False),
        # DATETIME is wall-clock: reading it as an instant shifts every value by the session offset.
        ("timestamp_ntz", "timestamp_ntz", True),
        ("timestamp_ntz", "timestamp", False),
        # A BigQuery TIMESTAMP *is* an instant, so the reverse must not be accepted either.
        ("timestamp", "timestamp", True),
        ("timestamp", "timestamp_ntz", False),
        ("decimal(38, 9)", "decimal(38,9)", True),
        ("decimal(38, 9)", "decimal(10,2)", False),
        # Multi-field STRUCT: BigQuery reports ", " between fields, which must not fail the compare.
        # Element types keep their BigQuery spelling, because a STRUCT only compares equal by
        # round-tripping the Databricks type back to BigQuery -- hence no rewriting below the top level.
        ("STRUCT<a INT64, b STRING>", "struct<a:bigint,b:string>", True),
        (
            "STRUCT<a INT64, b STRUCT<c FLOAT64, d DATETIME>>",
            "struct<a:bigint,b:struct<c:double,d:timestamp_ntz>>",
            True,
        ),
        ("STRUCT<n NUMERIC(38, 9), b STRING>", "struct<n:decimal(38,9),b:string>", True),
        ("ARRAY<NUMERIC(38, 9)>", "array<decimal(38,9)>", True),
    ],
)
def test_schema_compare_verdict_for_reported_types(source_datatype, databricks_datatype, is_valid):
    """The schema query's output has to survive SchemaCompare: an equivalent migration must validate and
    a lossy one must not."""
    master = SchemaMatchResult(
        source_column_normalized="v",
        source_column_normalized_ansi="v",
        source_datatype=source_datatype,
        databricks_column="v",
        databricks_datatype=databricks_datatype,
    )

    SchemaCompare._validate_parsed_query(get_dialect("bigquery"), master)  # pylint: disable=protected-access

    assert master.is_valid is is_valid


@pytest.mark.parametrize("dialect", ["bigquery", "snowflake", "oracle", "tsql", "redshift", "teradata"])
def test_schema_compare_normalizes_spacing_for_every_source(dialect):
    """A declared type may carry ", " where sqlglot renders ",". Both sides of the comparison are
    normalized, so this holds for every source rather than only the one that surfaced it."""
    master = SchemaMatchResult(
        source_column_normalized="v",
        source_column_normalized_ansi="v",
        source_datatype="decimal(38, 0)",
        databricks_column="v",
        databricks_datatype="decimal(38,0)",
    )

    SchemaCompare._validate_parsed_query(get_dialect(dialect), master)  # pylint: disable=protected-access

    assert master.is_valid


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
