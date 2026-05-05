import re
from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Table


def initial_setup():
    engine = get_dialect("tsql")
    reader = create_autospec(RemoteQueryReader)
    return engine, reader


def test_read_data_with_options():
    # initial setup
    engine, reader = initial_setup()

    # create object for MSSQLServerDataSource
    data_source = TSQLServerDataSource(engine, reader)
    # Create a Tables configuration object with JDBC reader options
    table_conf = Table(
        source_name="src_supplier",
        target_name="tgt_supplier",
        jdbc_reader_options=JdbcReaderOptions(
            num_partitions=100, partition_column="s_partition_key", lower_bound="0", upper_bound="100"
        ),
    )

    # Call the read_data method with the Tables configuration
    data_source.read_data(
        "org", "data", "employee", "WITH tmp AS (SELECT * from :tbl) select 1 from tmp", table_conf.jdbc_reader_options
    )

    # reader assertions — verify the query passed to remote_query reader
    reader.read_data.assert_called_once_with(
        "WITH tmp AS (SELECT * from data.[employee]) select 1 from tmp",
        "org",
        "database",
        "query",
        table_conf.jdbc_reader_options,
    )


def test_get_schema():
    # initial setup
    engine, reader = initial_setup()
    # Mocking get secret method to return the required values
    data_source = TSQLServerDataSource(engine, reader)
    # call test method
    data_source.get_schema("org", "schema", "supplier")
    # reader assertions — verify the schema query passed to remote_query reader
    reader.read_data.assert_called_once()
    call_args = reader.read_data.call_args
    source_query = call_args[0][0]
    expected_query = re.sub(
        r'\s+',
        ' ',
        r"""SELECT
                     COLUMN_NAME AS 'column_name',
                     CASE
                        WHEN DATA_TYPE IN ('int', 'bigint')
                            THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('smallint', 'tinyint')
                            THEN 'smallint'
                        WHEN DATA_TYPE IN ('decimal' ,'numeric')
                            THEN 'decimal(' +
                                CAST(NUMERIC_PRECISION AS VARCHAR) + ',' +
                                CAST(NUMERIC_SCALE AS VARCHAR) + ')'
                        WHEN DATA_TYPE IN ('float', 'real')
                                THEN 'double'
                        WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL AND DATA_TYPE IN ('varchar','char','text','nchar','nvarchar','ntext')
                                THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('date','time','datetime', 'datetime2','smalldatetime','datetimeoffset')
                                THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('bit')
                                THEN 'boolean'
                        WHEN DATA_TYPE IN ('binary','varbinary')
                                THEN 'binary'
                        ELSE DATA_TYPE
                    END AS 'data_type'
                    FROM
                        INFORMATION_SCHEMA.COLUMNS
                    WHERE LOWER(TABLE_NAME) = LOWER('supplier')
                    AND LOWER(TABLE_SCHEMA) = LOWER('schema')
                    AND LOWER(TABLE_CATALOG) = LOWER('org')
              """,
    )
    assert source_query == expected_query
    assert call_args[0][1] == "org"
    assert call_args[0][2] == "database"
    assert call_args[0][3] == "query"


def test_get_schema_exception_handling():
    # initial setup
    engine, reader = initial_setup()
    data_source = TSQLServerDataSource(engine, reader)

    reader.read_data.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            """Runtime exception occurred while fetching schema using SELECT COLUMN_NAME AS 'column_name', CASE WHEN DATA_TYPE IN ('int', 'bigint') THEN DATA_TYPE WHEN DATA_TYPE IN ('smallint', 'tinyint') THEN 'smallint' WHEN DATA_TYPE IN ('decimal' ,'numeric') THEN 'decimal(' + CAST(NUMERIC_PRECISION AS VARCHAR) + ',' + CAST(NUMERIC_SCALE AS VARCHAR) + ')' WHEN DATA_TYPE IN ('float', 'real') THEN 'double' WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL AND DATA_TYPE IN ('varchar','char','text','nchar','nvarchar','ntext') THEN DATA_TYPE WHEN DATA_TYPE IN ('date','time','datetime', 'datetime2','smalldatetime','datetimeoffset') THEN DATA_TYPE WHEN DATA_TYPE IN ('bit') THEN 'boolean' WHEN DATA_TYPE IN ('binary','varbinary') THEN 'binary' ELSE DATA_TYPE END AS 'data_type' FROM INFORMATION_SCHEMA.COLUMNS WHERE LOWER(TABLE_NAME) = LOWER('supplier') AND LOWER(TABLE_SCHEMA) = LOWER('schema') AND LOWER(TABLE_CATALOG) = LOWER('org')  : Test Exception"""
        ),
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
