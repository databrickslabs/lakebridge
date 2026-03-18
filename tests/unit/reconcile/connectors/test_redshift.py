import base64
import re
from unittest.mock import MagicMock, create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
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
            'port': GetSecretResponse(key='port', value=base64.b64encode(bytes('5439', 'utf-8')).decode('utf-8')),
            'database': GetSecretResponse(
                key='database', value=base64.b64encode(bytes('my_database', 'utf-8')).decode('utf-8')
            ),
        }
    }

    return secret_mock[scope][key]


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    engine = get_dialect("redshift")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"
    ws.secrets.get_secret.side_effect = mock_secret
    return engine, spark, ws, scope


def test_get_jdbc_url_happy():
    engine, spark, ws, scope = initial_setup()
    data_source = RedshiftDataSource(engine, spark, ws, scope)
    url = data_source.get_jdbc_url
    assert url == "jdbc:redshift://my_host:5439/my_database"


def test_read_data_with_out_options():
    engine, spark, ws, scope = initial_setup()
    rds = RedshiftDataSource(engine, spark, ws, scope)
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=None,
        join_columns=None,
    )

    rds.read_data(None, "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option.assert_called_with(
        "url",
        "jdbc:redshift://my_host:5439/my_database",
    )
    spark.read.format().option().option.assert_called_with("driver", "com.amazon.redshift.jdbc42.Driver")
    spark.read.format().option().option().option.assert_called_with("dbtable", "(select 1 from data.employee) tmp")
    actual_args = spark.read.format().option().option().option().options.call_args.kwargs
    expected_args = {
        "user": "my_user",
        "password": "my_password",
    }
    assert actual_args == expected_args
    spark.read.format().option().option().option().options().load.assert_called_once()


def test_read_data_with_options():
    engine, spark, ws, scope = initial_setup()
    rds = RedshiftDataSource(engine, spark, ws, scope)
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=50, partition_column="s_nationkey", lower_bound="0", upper_bound="100"
        ),
        join_columns=None,
    )

    rds.read_data(None, "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option.assert_called_with(
        "url",
        "jdbc:redshift://my_host:5439/my_database",
    )
    spark.read.format().option().option.assert_called_with("driver", "com.amazon.redshift.jdbc42.Driver")
    spark.read.format().option().option().option.assert_called_with("dbtable", "(select 1 from data.employee) tmp")
    jdbc_actual_args = spark.read.format().option().option().option().options.call_args.kwargs
    jdbc_expected_args = {
        "numPartitions": 50,
        "partitionColumn": "s_nationkey",
        "lowerBound": '0',
        "upperBound": "100",
        "fetchsize": 100,
        "user": "my_user",
        "password": "my_password",
    }
    assert jdbc_actual_args == jdbc_expected_args
    spark.read.format().option().option().option().options().load.assert_called_once()


def test_get_schema():
    engine, spark, ws, scope = initial_setup()
    rds = RedshiftDataSource(engine, spark, ws, scope)
    rds.get_schema(None, "data", "employee")
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option().option().option.assert_called_with(
        "dbtable",
        re.sub(
            r'\s+',
            ' ',
            r"""(SELECT
                     column_name,
                     CASE
                        WHEN data_type = 'numeric' AND numeric_precision IS NOT NULL
                            THEN 'decimal(' || numeric_precision || ',' || numeric_scale || ')'
                        WHEN data_type = 'real'
                            THEN 'float'
                        WHEN data_type = 'double precision'
                            THEN 'double'
                        WHEN data_type = 'character varying' AND character_maximum_length IS NOT NULL
                            THEN 'varchar(' || character_maximum_length || ')'
                        WHEN data_type = 'character' AND character_maximum_length IS NOT NULL
                            THEN 'char(' || character_maximum_length || ')'
                        WHEN data_type IN ('varbyte')
                            THEN 'binary'
                        ELSE data_type
                    END AS data_type
                    FROM
                        information_schema.columns
                    WHERE LOWER(table_name) = LOWER('employee')
                    AND LOWER(table_schema) = LOWER('data')
                    ORDER BY ordinal_position
                ) tmp""",
        ),
    )


def test_read_data_exception_handling():
    engine, spark, ws, scope = initial_setup()
    rds = RedshiftDataSource(engine, spark, ws, scope)
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=None,
        join_columns=None,
    )

    spark.read.format().option().option().option().options().load.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from data.employee : Test Exception",
    ):
        rds.read_data(None, "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)


def test_get_schema_exception_handling():
    engine, spark, ws, scope = initial_setup()
    rds = RedshiftDataSource(engine, spark, ws, scope)

    spark.read.format().option().option().option().options().load.side_effect = RuntimeError("Test Exception")

    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape(
            "Runtime exception occurred while fetching schema using SELECT column_name, CASE WHEN data_type = "
            "'numeric' AND numeric_precision IS NOT NULL THEN 'decimal(' || numeric_precision || ',' || "
            "numeric_scale || ')' WHEN data_type = 'real' THEN 'float' WHEN data_type = 'double precision' "
            "THEN 'double' WHEN data_type = 'character varying' AND character_maximum_length IS NOT NULL "
            "THEN 'varchar(' || character_maximum_length || ')' WHEN data_type = 'character' AND "
            "character_maximum_length IS NOT NULL THEN 'char(' || character_maximum_length || ')' WHEN "
            "data_type IN ('varbyte') THEN 'binary' ELSE data_type END AS data_type FROM "
            "information_schema.columns WHERE LOWER(table_name) = LOWER('employee') AND "
            "LOWER(table_schema) = LOWER('data') ORDER BY ordinal_position  : Test Exception"
        ),
    ):
        rds.get_schema(None, "data", "employee")


def test_normalize_identifier():
    engine, spark, ws, scope = initial_setup()
    data_source = RedshiftDataSource(engine, spark, ws, scope)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
