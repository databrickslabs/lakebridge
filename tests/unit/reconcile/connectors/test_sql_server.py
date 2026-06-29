import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


def initial_setup():
    engine = get_dialect("tsql")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for MSSQLServerDataSource
    data_source = TSQLServerDataSource(engine, reader)
    opts = JdbcReaderOptions(num_partitions=100, partition_column="s_partition_key", lower_bound="0", upper_bound="100")

    # Call the read_data method with the Tables configuration
    data_source.read_data("org", "data", "employee", "WITH tmp AS (SELECT * from :tbl) select 1 from tmp", opts)

    # reader assertions — verify the query passed to remote_query reader
    reader.read_data.assert_called_once_with(
        "WITH tmp AS (SELECT * from data.[employee]) select 1 from tmp", "org", "database", "query", opts
    )


def test_get_schema_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    data_source = TSQLServerDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape("Runtime exception occurred while fetching schema"),
    ):
        data_source.get_schema("org", "schema", "supplier")


def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = TSQLServerDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", "[a]")
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", "[b]")
    assert data_source.normalize_identifier("[c]") == NormalizedIdentifier("`c`", "[c]")
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '[`e`f`]')
    assert data_source.normalize_identifier('`e``f`') == NormalizedIdentifier("`e``f`", '[e`f]')
    assert data_source.normalize_identifier('[ g h ]') == NormalizedIdentifier("` g h `", '[ g h ]')
    assert data_source.normalize_identifier('[[i]]]') == NormalizedIdentifier("`[i]`", '[[i]]]')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '["j"k"]')
