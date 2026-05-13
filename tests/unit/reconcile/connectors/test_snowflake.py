import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Table


def initial_setup():
    engine = get_dialect("snowflake")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_out_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, reader)
    # Create a Tables configuration object with no JDBC reader options
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
    )

    # Call the read_data method with the Tables configuration
    dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    # reader assertions — verify the query passed to remote_query reader
    reader.read_data.assert_called_once_with(
        "select 1 from org.data.employee",
        "org",
        "database",
        "query",
    )


def test_read_data_with_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, reader)
    # Create a Tables configuration object with JDBC reader options
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=JdbcReaderOptions(
            num_partitions=100, partition_column="s_nationkey", lower_bound="0", upper_bound="100"
        ),
        select_columns=None,
        drop_columns=None,
        join_columns=None,
        column_mapping=None,
        transformations=None,
        filters=None,
        column_thresholds=None,
    )

    # Call the read_data method with the Tables configuration
    dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    # reader assertions — verify the query passed to remote_query reader
    # Note: SnowflakeDataSource.read_data does not pass options to reader.read_data (options are unused for snowflake)
    reader.read_data.assert_called_once_with(
        "select 1 from org.data.employee",
        "org",
        "database",
        "query",
    )


def test_get_schema():
    # initial setup
    engine, reader = initial_setup()
    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, reader)
    # call test method
    dfds.get_schema("catalog", "schema", "supplier")
    # reader assertions — verify the schema query passed to remote_query reader
    reader.read_data.assert_called_once()
    call_args = reader.read_data.call_args
    source_query = call_args[0][0]
    expected_query = re.sub(
        r'\s+',
        ' ',
        """select column_name, case when numeric_precision is not null and numeric_scale is not null then
        concat(data_type, '(', numeric_precision, ',' , numeric_scale, ')') when lower(data_type) = 'text' then
        concat('varchar', '(', CHARACTER_MAXIMUM_LENGTH, ')')  else data_type end as data_type from
        catalog.INFORMATION_SCHEMA.COLUMNS where lower(table_name)='supplier' and table_schema = 'SCHEMA'
        order by ordinal_position""",
    )
    assert source_query == expected_query
    assert call_args[0][1] == "catalog"
    assert call_args[0][2] == "database"
    assert call_args[0][3] == "query"


def test_read_data_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    dfds = SnowflakeDataSource(engine, reader)
    # Create a Tables configuration object
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=None,
        join_columns=None,
        select_columns=None,
        drop_columns=None,
        column_mapping=None,
        transformations=None,
        column_thresholds=None,
        filters=None,
    )

    reader.read_data.side_effect = RuntimeError("Test Exception")

    # Call the read_data method with the Tables configuration and assert that a PySparkException is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from org.data.employee : Test Exception",
    ):
        dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)


def test_get_schema_exception_handling():
    # initial setup
    engine, reader = initial_setup()

    dfds = SnowflakeDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            "Runtime exception occurred while fetching schema using select column_name, case when numeric_precision "
            "is not null and numeric_scale is not null then concat(data_type, '(', numeric_precision, ',' , "
            "numeric_scale, ')') when lower(data_type) = 'text' then concat('varchar', '(', "
            "CHARACTER_MAXIMUM_LENGTH, ')') else data_type end as data_type from catalog.INFORMATION_SCHEMA.COLUMNS "
            "where lower(table_name)='supplier' and table_schema = 'SCHEMA' order by ordinal_position : Test "
            "Exception"
        ),
    ):
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
