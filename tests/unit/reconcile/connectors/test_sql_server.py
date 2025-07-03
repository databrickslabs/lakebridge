import base64
import re
from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Table
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import GetSecretResponse


def mock_secret(scope, key):
    scope_secret_mock = {
        "scope": {
            'user': GetSecretResponse(key='user', value=base64.b64encode('my_user'.encode('utf-8')).decode('utf-8')),
            'password': GetSecretResponse(
                key='password', value=base64.b64encode(bytes('my_password', 'utf-8')).decode('utf-8')
            ),
            'host': GetSecretResponse(key='host', value=base64.b64encode(bytes('my_host', 'utf-8')).decode('utf-8')),
            'port': GetSecretResponse(key='port', value=base64.b64encode(bytes('777', 'utf-8')).decode('utf-8')),
            'database': GetSecretResponse(
                key='database', value=base64.b64encode(bytes('my_database', 'utf-8')).decode('utf-8')
            ),
            'encrypt': GetSecretResponse(key='encrypt', value=base64.b64encode(bytes('true', 'utf-8')).decode('utf-8')),
            'trustServerCertificate': GetSecretResponse(
                key='trustServerCertificate', value=base64.b64encode(bytes('true', 'utf-8')).decode('utf-8')
            ),
        }
    }

    return scope_secret_mock[scope][key]


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    # Define the source, workspace, and scope
    engine = get_dialect("tsql")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"
    ws.secrets.get_secret.side_effect = mock_secret
    return engine, spark, ws, scope


def test_get_jdbc_url_happy():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    # create object for TSQLServerDataSource
    data_source = TSQLServerDataSource(engine, spark, ws, scope)
    url = data_source.get_jdbc_url
    # Assert that the URL is generated correctly
    assert url == (
        """jdbc:sqlserver://my_host:777;databaseName=my_database;user=my_user;password=my_password;encrypt=true;trustServerCertificate=true;"""
    )


def test_get_jdbc_url_fail():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    ws.secrets.get_secret.side_effect = mock_secret
    # create object for TSQLServerDataSource
    data_source = TSQLServerDataSource(engine, spark, ws, scope)
    url = data_source.get_jdbc_url
    # Assert that the URL is generated correctly
    assert url == (
        """jdbc:sqlserver://my_host:777;databaseName=my_database;user=my_user;password=my_password;encrypt=true;trustServerCertificate=true;"""
    )


def test_read_data_with_options():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for MSSQLServerDataSource
    data_source = TSQLServerDataSource(engine, spark, ws, scope)
    # Create a Tables configuration object with JDBC reader options
    table_conf = Table(
        source_name="src_supplier",
        target_name="tgt_supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=100, partition_column="s_partition_key", lower_bound="0", upper_bound="100"
        ),
    )

    # Call the read_data method with the Tables configuration
    data_source.read_data(
        "org", "data", "employee", "WITH tmp AS (SELECT * from :tbl) select 1 from tmp", table_conf.jdbc_reader_options
    )

    # spark assertions
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option.assert_called_with(
        "url",
        "jdbc:sqlserver://my_host:777;databaseName=my_database;user=my_user;password=my_password;encrypt=true;trustServerCertificate=true;",
    )
    spark.read.format().option().option.assert_called_with("driver", "com.microsoft.sqlserver.jdbc.SQLServerDriver")
    spark.read.format().option().option().option.assert_called_with(
        "dbtable", "(WITH tmp AS (SELECT * from org.data.employee) select 1 from tmp) tmp"
    )
    actual_args = spark.read.format().option().option().option().options.call_args.kwargs
    expected_args = {
        "numPartitions": 100,
        "partitionColumn": "s_partition_key",
        "lowerBound": '0',
        "upperBound": "100",
        "fetchsize": 100,
    }
    assert actual_args == expected_args
    spark.read.format().option().option().option().options().load.assert_called_once()


def test_get_schema():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    # Mocking get secret method to return the required values
    data_source = TSQLServerDataSource(engine, spark, ws, scope)
    # call test method
    data_source.get_schema("org", "schema", "supplier")
    # spark assertions
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option().option().option.assert_called_with(
        "dbtable",
        re.sub(
            r'\s+',
            ' ',
            r"""(SELECT
                     COLUMN_NAME,
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
                    END AS 'DATA_TYPE'
                    FROM
                        INFORMATION_SCHEMA.COLUMNS
                    WHERE LOWER(TABLE_NAME) = LOWER('supplier')
                    AND LOWER(TABLE_SCHEMA) = LOWER('schema')
                    AND LOWER(TABLE_CATALOG) = LOWER('org')
                ) tmp""",
        ),
    )


def test_get_schema_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    data_source = TSQLServerDataSource(engine, spark, ws, scope)

    spark.read.format().option().option().option().option().load.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            """Runtime exception occurred while fetching schema using SELECT COLUMN_NAME, CASE WHEN DATA_TYPE IN ('int', 'bigint') THEN DATA_TYPE WHEN DATA_TYPE IN ('smallint', 'tinyint') THEN 'smallint' WHEN DATA_TYPE IN ('decimal' ,'numeric') THEN 'decimal(' + CAST(NUMERIC_PRECISION AS VARCHAR) + ',' + CAST(NUMERIC_SCALE AS VARCHAR) + ')' WHEN DATA_TYPE IN ('float', 'real') THEN 'double' WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL AND DATA_TYPE IN ('varchar','char','text','nchar','nvarchar','ntext') THEN DATA_TYPE WHEN DATA_TYPE IN ('date','time','datetime', 'datetime2','smalldatetime','datetimeoffset') THEN DATA_TYPE WHEN DATA_TYPE IN ('bit') THEN 'boolean' WHEN DATA_TYPE IN ('binary','varbinary') THEN 'binary' ELSE DATA_TYPE END AS 'DATA_TYPE' FROM INFORMATION_SCHEMA.COLUMNS WHERE LOWER(TABLE_NAME) = LOWER('supplier') AND LOWER(TABLE_SCHEMA) = LOWER('schema') AND LOWER(TABLE_CATALOG) = LOWER('org')  : Test Exception"""
        ),
    ):
        data_source.get_schema("org", "schema", "supplier")
