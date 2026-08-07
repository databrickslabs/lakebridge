import re
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

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


def _schema_df(*columns):
    """Fake the DataFrame the schema query returns, so get_schema can be driven through its public API."""
    rows = [SimpleNamespace(column_name=name, data_type=dtype) for name, dtype in columns]
    df = MagicMock()
    df.columns = ["column_name", "data_type"]
    df.select.return_value.collect.return_value = rows
    return df


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
    # Types whose Databricks spelling is not valid BigQuery are not reported: the same string is the
    # CAST target in the sampling query, so `timestamp_ntz`/`bigint`/`double` would fail on BigQuery.
    assert "timestamp_ntz" not in schema_query
    assert "'bigint'" not in schema_query
    assert "'double'" not in schema_query
    # BIGNUMERIC has no exact Databricks equivalent, so it passes through rather than truncating.
    assert "BIGNUMERIC" not in schema_query


@pytest.mark.parametrize(
    "data_type, expect_warning",
    [
        # A lossy target compares as equal for these, so they are flagged for manual review.
        ("int64", True),
        ("float64", True),
        ("datetime", True),
        ("array<int64>", True),
        ("array<datetime>", True),
        # No Databricks equivalent: a correct migration may be reported as a mismatch.
        ("bignumeric", True),
        ("time", True),
        ("range<date>", True),
        # Types schema compare judges exactly must stay silent, or the warning becomes noise.
        ("string", False),
        ("date", False),
        ("bool", False),
        ("bytes", False),
        ("decimal(38, 9)", False),
        ("numeric(10, 2)", False),
        ("timestamp", False),
        ("geography", False),
    ],
)
def test_warns_only_for_types_schema_compare_cannot_judge(caplog, data_type, expect_warning):
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)
    reader.read_data.return_value = _schema_df(("amount_zz", data_type))

    with caplog.at_level("WARNING"):
        data_source.get_schema("proj", "dataset", "supplier")

    # Assert on the column name, not a substring of the message itself.
    assert ("amount_zz" in caplog.text) is expect_warning


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
    assert "CAST(`amount` AS STRING)" in query
    assert "CAST(`name` AS STRING)" in query
    # sqlglot renders the :tbl placeholder as @tbl for BigQuery — read_data handles both
    assert "@tbl" in query or ":tbl" in query


def test_hash_query_formats_timestamps_the_way_spark_casts_them():
    """CAST AS STRING appends a UTC offset and pads fractional seconds, changing the row hash.
    Verified equal against live BigQuery and Databricks."""
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)

    # `datetime` is what _SCHEMA_QUERY reports; `timestamp_ntz` reaches the mapping under a
    # user-supplied schema, and both must format identically or the two paths would disagree.
    query = _build_hash_query(
        data_source,
        engine,
        [
            ("id", "int64"),
            ("ts", "timestamp"),
            ("dt", "datetime"),
            ("dt_ntz", "timestamp_ntz"),
        ],
    )

    # %E*S emits only the fractional digits present, as Spark does; 'UTC' drops the +00 suffix.
    assert "FORMAT_TIMESTAMP('%F %H:%M:%E*S', `ts`, 'UTC')" in query
    assert "FORMAT_DATETIME('%F %H:%M:%E*S', `dt`)" in query
    assert "FORMAT_DATETIME('%F %H:%M:%E*S', `dt_ntz`)" in query
    assert "CAST(`ts` AS STRING)" not in query
    assert "CAST(`dt` AS STRING)" not in query


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
