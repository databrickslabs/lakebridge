import re
from unittest.mock import create_autospec

import pytest

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


def test_read_data_builds_two_part_backtick_quoted_name():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    # catalog ("proj") is ignored: the project is abstracted by the UC connection, so the table
    # is referenced two-part as `dataset`.`table` (the connection's default project scopes it).
    dfds.read_data("proj", "dataset", "employee", "select 1 from :tbl", None)

    # BigQuery remote_query rejects `database`; results materialize into the dataset (no project option)
    reader.read_data.assert_called_once_with(
        "select 1 from `dataset`.`employee`",
        "dataset",
        "materializationDataset",
        "query",
        None,
    )


def test_read_data_substitutes_bigquery_rendered_placeholder():
    # sqlglot's BigQuery generator renders the `:tbl` placeholder as `@tbl`; read_data must handle it.
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.read_data("proj", "dataset", "employee", "select 1 from @tbl", None)

    reader.read_data.assert_called_once_with(
        "select 1 from `dataset`.`employee`",
        "dataset",
        "materializationDataset",
        "query",
        None,
    )


def test_read_data_uses_configured_materialization_dataset():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader, materialization_dataset="scratch_ds")

    dfds.read_data("proj", "dataset", "employee", "select 1 from :tbl", None)

    reader.read_data.assert_called_once_with(
        "select 1 from `dataset`.`employee`",
        "scratch_ds",
        "materializationDataset",
        "query",
        None,
    )


def test_read_data_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            "Runtime exception occurred while fetching data using "
            "select 1 from `dataset`.`employee` : Test Exception"
        ),
    ):
        dfds.read_data("proj", "dataset", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(DataSourceRuntimeException, match=re.escape("Runtime exception occurred while fetching schema")):
        dfds.get_schema("proj", "dataset", "supplier")


def test_get_schema_query_targets_information_schema_with_type_canonicalization():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    dfds.get_schema("proj", "dataset", "supplier")

    schema_query = reader.read_data.call_args.args[0]
    assert "`dataset`.INFORMATION_SCHEMA.COLUMNS" in schema_query
    assert "where table_name = 'supplier'" in schema_query
    # Stage-1 canonicalization for the BQ types sqlglot cannot bridge to Databricks on its own
    assert "when data_type like 'BIGNUMERIC%' then 'string'" in schema_query
    assert "when data_type = 'NUMERIC' then 'decimal(38,9)'" in schema_query
    assert "when data_type = 'TIME' then 'string'" in schema_query
    assert "when data_type = 'JSON' then 'variant'" in schema_query
    assert "when data_type = 'RANGE<DATE>' then 'struct<start date, end date>'" in schema_query


def test_list_schemas_and_tables():
    engine, reader = initial_setup()
    dfds = BigQueryDataSource(engine, reader)

    # SCHEMATA is project-level and unqualified (the connection's default project scopes it).
    dfds.list_schemas("proj")
    schemas_query = reader.read_data.call_args.args[0]
    assert "INFORMATION_SCHEMA.SCHEMATA" in schemas_query
    assert "`proj`" not in schemas_query

    dfds.list_tables("proj", "dataset")
    assert "`dataset`.INFORMATION_SCHEMA.TABLES" in reader.read_data.call_args.args[0]


def test_hash_query_emits_bigquery_compatible_sql():
    # Regression for the hash path: BigQuery needs a Dialect_hash_algo_mapping entry (else ValueError)
    # and a cast-to-STRING transform (else CONCAT/TRIM fail on non-string columns).
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)
    cols = [("id", "int64"), ("amount", "decimal(38,9)"), ("name", "string")]
    schema = []
    for name, dtype in cols:
        norm = data_source.normalize_identifier(name)
        schema.append(Schema(norm.ansi_normalized, dtype, norm.ansi_normalized, norm.source_normalized))
    table_conf = Table(source_name="t", target_name="t", join_columns=["id"])

    query = HashQueryBuilder(table_conf, schema, "source", engine, data_source).build_query("data")

    # hex-wrapped SHA-256 (matches Databricks sha2(...,256))
    assert "TO_HEX(SHA256(" in query
    # non-decimal columns cast to STRING for concatenation
    assert "CAST(`id` AS STRING)" in query
    # decimals use scale-aware FORMAT (not CAST) so trailing zeros match Spark's DECIMAL(38,9) string
    assert "FORMAT('%.9f', `amount`)" in query
    assert "CAST(`amount` AS STRING)" not in query
    # sqlglot renders the :tbl placeholder as @tbl for BigQuery — read_data handles both
    assert "@tbl" in query or ":tbl" in query


def test_hash_query_decimal_format_is_scale_aware():
    # parameterized decimal scale must drive the FORMAT precision so BQ padding matches Spark's scale
    engine, reader = initial_setup()
    data_source = BigQueryDataSource(engine, reader)
    norm = data_source.normalize_identifier("amount")
    schema = [Schema(norm.ansi_normalized, "decimal(10,2)", norm.ansi_normalized, norm.source_normalized)]
    table_conf = Table(source_name="t", target_name="t", join_columns=["amount"])

    query = HashQueryBuilder(table_conf, schema, "source", engine, data_source).build_query("row")

    assert "FORMAT('%.2f', `amount`)" in query


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
