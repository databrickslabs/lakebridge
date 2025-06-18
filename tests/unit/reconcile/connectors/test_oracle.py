import base64
import re
from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Table
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import GetSecretResponse


def mock_secret(scope, key):
    secret_mock = {
        "scope": {
            'user': GetSecretResponse(key='user', value=base64.b64encode(bytes('my_user', 'utf-8')).decode('utf-8')),
            'password': GetSecretResponse(
                key='password', value=base64.b64encode(bytes('my_password', 'utf-8')).decode('utf-8')
            ),
            'host': GetSecretResponse(key='host', value=base64.b64encode(bytes('my_host', 'utf-8')).decode('utf-8')),
            'port': GetSecretResponse(key='port', value=base64.b64encode(bytes('777', 'utf-8')).decode('utf-8')),
            'database': GetSecretResponse(
                key='database', value=base64.b64encode(bytes('my_database', 'utf-8')).decode('utf-8')
            ),
        }
    }

    return secret_mock[scope][key]


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    # Define the source, workspace, and scope
    engine = get_dialect("oracle")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"
    ws.secrets.get_secret.side_effect = mock_secret
    return engine, spark, ws, scope


def test_read_data_with_options():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for SnowflakeDataSource
    ords = OracleDataSource(engine, spark, ws, scope)
    # Create a Tables configuration object with JDBC reader options
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=50, partition_column="s_nationkey", lower_bound="0", upper_bound="100"
        ),
        join_columns=None,
        select_columns=None,
        drop_columns=None,
        column_mapping=None,
        transformations=None,
        column_thresholds=None,
        filters=None,
    )

    # Call the read_data method with the Tables configuration
    ords.read_data(None, "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    # spark assertions
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option.assert_called_with(
        "url",
        "jdbc:oracle:thin:my_user/my_password@//my_host:777/my_database",
    )
    spark.read.format().option().option.assert_called_with("driver", "oracle.jdbc.driver.OracleDriver")
    spark.read.format().option().option().option.assert_called_with("dbtable", "(select 1 from data.employee) tmp")
    actual_args = spark.read.format().option().option().option().options.call_args.kwargs
    expected_args = {
        "numPartitions": 50,
        "partitionColumn": "s_nationkey",
        "lowerBound": '0',
        "upperBound": "100",
        "fetchsize": 100,
        "oracle.jdbc.mapDateToTimestamp": "False",
        "sessionInitStatement": r"BEGIN dbms_session.set_nls('nls_date_format', "
        r"'''YYYY-MM-DD''');dbms_session.set_nls('nls_timestamp_format', '''YYYY-MM-DD "
        r"HH24:MI:SS''');END;",
    }
    assert actual_args == expected_args
    spark.read.format().option().option().option().options().load.assert_called_once()


def test_get_schema():
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for SnowflakeDataSource
    ords = OracleDataSource(engine, spark, ws, scope)
    # call test method
    ords.get_schema(None, "data", "employee")
    # spark assertions
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option().option().option.assert_called_with(
        "dbtable",
        re.sub(
            r'\s+',
            ' ',
            r"""(select column_name, case when (data_precision is not null
                                              and data_scale <> 0)
                                              then data_type || '(' || data_precision || ',' || data_scale || ')'
                                              when (data_precision is not null and data_scale = 0)
                                              then data_type || '(' || data_precision || ')'
                                              when data_precision is null and (lower(data_type) in ('date') or
                                              lower(data_type) like 'timestamp%') then  data_type
                                              when CHAR_LENGTH = 0 then data_type
                                              else data_type || '(' || CHAR_LENGTH || ')'
                                              end data_type
                                              FROM ALL_TAB_COLUMNS
                            WHERE lower(TABLE_NAME) = 'employee' and lower(owner) = 'data') tmp""",
        ),
    )


def test_read_data_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    ords = OracleDataSource(engine, spark, ws, scope)
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

    spark.read.format().option().option().option().options().load.side_effect = RuntimeError("Test Exception")

    # Call the read_data method with the Tables configuration and assert that a PySparkException is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from data.employee : Test Exception",
    ):
        ords.read_data(None, "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)


def test_get_schema_exception_handling():
    # initial setup
    engine, spark, ws, scope = initial_setup()
    ords = OracleDataSource(engine, spark, ws, scope)

    spark.read.format().option().option().option().load.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=r"""select column_name, case when (data_precision is not null
                                                  and data_scale <> 0)
                                                  then data_type || '(' || data_precision || ',' || data_scale || ')'
                                                  when (data_precision is not null and data_scale = 0)
                                                  then data_type || '(' || data_precision || ')'
                                                  when data_precision is null and (lower(data_type) in ('date') or
                                                  lower(data_type) like 'timestamp%') then  data_type
                                                  when CHAR_LENGTH = 0 then data_type
                                                  else data_type || '(' || CHAR_LENGTH || ')'
                                                  end data_type
                                                  FROM ALL_TAB_COLUMNS
                                WHERE lower(TABLE_NAME) = 'employee' and lower(owner) = 'data' """,
    ):
        ords.get_schema(None, "data", "employee")
