import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException


def initial_setup():
    engine = get_dialect("snowflake")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_out_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, reader)

    # Call the read_data method with the Tables configuration
    dfds.read_data("org", "data", "employee", "select 1 from :tbl", None)

    # reader assertions — verify the query passed to remote_query reader
    reader.read_data.assert_called_once_with(
        "select 1 from org.data.employee",
        "org",
        "database",
        "query",
    )


def test_read_data_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    dfds = SnowflakeDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from org.data.employee : Test Exception",
    ):
        dfds.read_data("org", "data", "employee", "select 1 from :tbl", None)


def test_get_schema_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    dfds = SnowflakeDataSource(engine, reader)
    reader.read_data.side_effect = RuntimeError("Test Exception")

    with pytest.raises(DataSourceRuntimeException, match=re.escape("Runtime exception occurred while fetching schema")):
        dfds.get_schema("catalog", "schema", "supplier")


@pytest.mark.skip("Turned off till we can handle case sensitivity.")
def test_normalize_identifier():
    engine, reader = initial_setup()
    data_source = SnowflakeDataSource(engine, reader)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
