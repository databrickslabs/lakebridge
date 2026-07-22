from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


def initial_setup():
    engine = get_dialect("oracle")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for OracleDataSource
    ords = OracleDataSource(engine, reader)
    # Create a Tables configuration object with JDBC reader options
    opts = JdbcReaderOptions(num_partitions=50, partition_column="s_nationkey", lower_bound="0", upper_bound="100")
    # Call the read_data method with the Tables configuration
    ords.read_data("ORCL", "data", "employee", "select 1 from :tbl", opts)

    # reader assertions — verify the query passed to remote_query reader
    reader.read_data.assert_called_once_with("select 1 from data.employee", "ORCL", "service_name", "query", opts)


def test_read_data_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    ords = OracleDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from data.employee : Test Exception",
    ):
        ords.read_data("ORCL", "data", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    ords = OracleDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(DataSourceRuntimeException, match="Runtime exception occurred while fetching schema"):
        ords.get_schema("ORCL", "data", "employee")


@pytest.mark.skip("Turned off till we can handle case sensitivity.")
def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = OracleDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
