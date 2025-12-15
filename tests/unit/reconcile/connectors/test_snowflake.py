import base64
import re
from unittest.mock import MagicMock, create_autospec

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from databricks.labs.lakebridge.config import ReconcileCredentialsConfig
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import NormalizedIdentifier
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException, InvalidSnowflakePemPrivateKey
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Table
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import GetSecretResponse
from databricks.sdk.errors import NotFound


def mock_secret(scope, key):
    secret_mock = {
        "scope": {
            'sfUser': GetSecretResponse(
                key='sfUser', value=base64.b64encode(bytes('my_user', 'utf-8')).decode('utf-8')
            ),
            'sfPassword': GetSecretResponse(
                key='sfPassword', value=base64.b64encode(bytes('my_password', 'utf-8')).decode('utf-8')
            ),
            'sfDatabase': GetSecretResponse(
                key='sfDatabase', value=base64.b64encode(bytes('my_database', 'utf-8')).decode('utf-8')
            ),
            'sfSchema': GetSecretResponse(
                key='sfSchema', value=base64.b64encode(bytes('my_schema', 'utf-8')).decode('utf-8')
            ),
            'sfWarehouse': GetSecretResponse(
                key='sfWarehouse', value=base64.b64encode(bytes('my_warehouse', 'utf-8')).decode('utf-8')
            ),
            'sfRole': GetSecretResponse(
                key='sfRole', value=base64.b64encode(bytes('my_role', 'utf-8')).decode('utf-8')
            ),
            'sfUrl': GetSecretResponse(
                key='sfUrl', value=base64.b64encode(bytes('my_account.snowflakecomputing.com', 'utf-8')).decode('utf-8')
            ),
        }
    }

    return secret_mock[scope][key]


@pytest.fixture()
def snowflake_creds():
    def _snowflake_creds(scope, use_private_key=False, use_pem_password=False):
        creds = {
            'sfUser': f'{scope}/sfUser',
            'sfDatabase': f'{scope}/sfDatabase',
            'sfSchema': f'{scope}/sfSchema',
            'sfWarehouse': f'{scope}/sfWarehouse',
            'sfRole': f'{scope}/sfRole',
            'sfUrl': f'{scope}/sfUrl',
        }

        if use_private_key:
            creds['pem_private_key'] = f'{scope}/pem_private_key'
            if use_pem_password:
                creds['pem_private_key_password'] = f'{scope}/pem_private_key_password'
        else:
            creds['sfPassword'] = f'{scope}/sfPassword'

        return creds

    return _snowflake_creds


def generate_pkcs8_pem_key(malformed=False):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_key = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')
    return pem_key[:50] + "MALFORMED" + pem_key[60:] if malformed else pem_key


def mock_private_key_secret(scope, key):
    if key == 'pem_private_key':
        return GetSecretResponse(key=key, value=base64.b64encode(generate_pkcs8_pem_key().encode()).decode())
    if key == 'pem_private_key_password':
        return GetSecretResponse(key=key, value=b''.decode())
    return mock_secret(scope, key)


def mock_malformed_private_key_secret(scope, key):
    if key == 'pem_private_key':
        return GetSecretResponse(key=key, value=base64.b64encode(generate_pkcs8_pem_key(True).encode()).decode())
    if key == 'pem_private_key_password':
        return GetSecretResponse(key=key, value=b''.decode())
    return mock_secret(scope, key)


def mock_no_auth_key_secret(scope, key):
    if key in {'pem_private_key', 'sfPassword'}:
        raise NotFound("Secret not found")
    return mock_secret(scope, key)


def initial_setup():
    pyspark_sql_session = MagicMock()
    spark = pyspark_sql_session.SparkSession.builder.getOrCreate()

    # Define the source, workspace, and scope
    engine = get_dialect("snowflake")
    ws = create_autospec(WorkspaceClient)
    scope = "scope"
    ws.secrets.get_secret.side_effect = mock_secret
    return engine, spark, ws, scope


def test_get_jdbc_url_happy(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()
    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))
    url = dfds.get_jdbc_url
    # Assert that the URL is generated correctly
    assert url == (
        "jdbc:snowflake://my_account.snowflakecomputing.com"
        "/?user=my_user&password=my_password"
        "&db=my_database&schema=my_schema"
        "&warehouse=my_warehouse&role=my_role"
    )


def test_read_data_with_out_options(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))
    # Create a Tables configuration object with no JDBC reader options
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
    )

    # Call the read_data method with the Tables configuration
    dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)

    # spark assertions
    spark.read.format.assert_called_with("snowflake")
    spark.read.format().option.assert_called_with("dbtable", "(select 1 from org.data.employee) as tmp")
    spark.read.format().option().options.assert_called_with(
        sfUrl="my_account.snowflakecomputing.com",
        sfUser="my_user",
        sfPassword="my_password",
        sfDatabase="my_database",
        sfSchema="my_schema",
        sfWarehouse="my_warehouse",
        sfRole="my_role",
    )
    spark.read.format().option().options().load.assert_called_once()


def test_read_data_with_options(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()

    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))
    # Create a Tables configuration object with JDBC reader options
    table_conf = Table(
        source_name="supplier",
        target_name="supplier",
        jdbc_reader_options=JdbcReaderOptions(
            number_partitions=100, partition_column="s_nationkey", lower_bound="0", upper_bound="100"
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

    # spark assertions
    spark.read.format.assert_called_with("jdbc")
    spark.read.format().option.assert_called_with(
        "url",
        "jdbc:snowflake://my_account.snowflakecomputing.com/?user=my_user&password="
        "my_password&db=my_database&schema=my_schema&warehouse=my_warehouse&role=my_role",
    )
    spark.read.format().option().option.assert_called_with("driver", "net.snowflake.client.jdbc.SnowflakeDriver")
    spark.read.format().option().option().option.assert_called_with("dbtable", "(select 1 from org.data.employee) tmp")
    spark.read.format().option().option().option().options.assert_called_with(
        numPartitions=100, partitionColumn='s_nationkey', lowerBound='0', upperBound='100', fetchsize=100
    )
    spark.read.format().option().option().option().options().load.assert_called_once()


def test_get_schema(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()
    # Mocking get secret method to return the required values
    # create object for SnowflakeDataSource
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))
    # call test method
    dfds.get_schema("catalog", "schema", "supplier")
    # spark assertions
    spark.read.format.assert_called_with("snowflake")
    spark.read.format().option.assert_called_with(
        "dbtable",
        re.sub(
            r'\s+',
            ' ',
            """(select column_name, case when numeric_precision is not null and numeric_scale is not null then
        concat(data_type, '(', numeric_precision, ',' , numeric_scale, ')') when lower(data_type) = 'text' then
        concat('varchar', '(', CHARACTER_MAXIMUM_LENGTH, ')')  else data_type end as data_type from
        catalog.INFORMATION_SCHEMA.COLUMNS where lower(table_name)='supplier' and table_schema = 'SCHEMA'
        order by ordinal_position) as tmp""",
        ),
    )
    spark.read.format().option().options.assert_called_with(
        sfUrl="my_account.snowflakecomputing.com",
        sfUser="my_user",
        sfPassword="my_password",
        sfDatabase="my_database",
        sfSchema="my_schema",
        sfWarehouse="my_warehouse",
        sfRole="my_role",
    )
    spark.read.format().option().options().load.assert_called_once()


def test_read_data_exception_handling(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))
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

    spark.read.format().option().options().load.side_effect = RuntimeError("Test Exception")

    # Call the read_data method with the Tables configuration and assert that a PySparkException is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match="Runtime exception occurred while fetching data using select 1 from org.data.employee : Test Exception",
    ):
        dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)


def test_get_schema_exception_handling(snowflake_creds):
    # initial setup
    engine, spark, ws, scope = initial_setup()

    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope)))

    spark.read.format().option().options().load.side_effect = RuntimeError("Test Exception")

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=r"Runtime exception occurred while fetching schema using select column_name, case when numeric_precision "
        "is not null and numeric_scale is not null then concat\\(data_type, '\\(', numeric_precision, ',' , "
        "numeric_scale, '\\)'\\) when lower\\(data_type\\) = 'text' then concat\\('varchar', '\\(', "
        "CHARACTER_MAXIMUM_LENGTH, '\\)'\\) else data_type end as data_type from catalog.INFORMATION_SCHEMA.COLUMNS "
        "where lower\\(table_name\\)='supplier' and table_schema = 'SCHEMA' order by ordinal_position : Test "
        "Exception",
    ):
        dfds.get_schema("catalog", "schema", "supplier")


def test_read_data_without_options_private_key(snowflake_creds):
    engine, spark, ws, scope = initial_setup()
    ws.secrets.get_secret.side_effect = mock_private_key_secret
    dfds = SnowflakeDataSource(engine, spark, ws)
    dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope, use_private_key=True)))
    table_conf = Table(source_name="supplier", target_name="supplier")
    dfds.read_data("org", "data", "employee", "select 1 from :tbl", table_conf.jdbc_reader_options)
    spark.read.format.assert_called_with("snowflake")
    spark.read.format().option.assert_called_with("dbtable", "(select 1 from org.data.employee) as tmp")
    expected_options = {
        "sfUrl": "my_account.snowflakecomputing.com",
        "sfUser": "my_user",
        "sfDatabase": "my_database",
        "sfSchema": "my_schema",
        "sfWarehouse": "my_warehouse",
        "sfRole": "my_role",
    }
    actual_options = spark.read.format().option().options.call_args[1]
    actual_options.pop("pem_private_key", None)
    assert actual_options == expected_options
    spark.read.format().option().options().load.assert_called_once()


def test_read_data_without_options_malformed_private_key(snowflake_creds):
    engine, spark, ws, scope = initial_setup()
    ws.secrets.get_secret.side_effect = mock_malformed_private_key_secret
    dfds = SnowflakeDataSource(engine, spark, ws)

    with pytest.raises(InvalidSnowflakePemPrivateKey, match="Failed to load or process the provided PEM private key."):
        dfds.load_credentials(ReconcileCredentialsConfig("databricks", snowflake_creds(scope, use_private_key=True)))


def test_read_data_without_any_auth(snowflake_creds):
    engine, spark, ws, scope = initial_setup()
    ws.secrets.get_secret.side_effect = mock_no_auth_key_secret
    dfds = SnowflakeDataSource(engine, spark, ws)
    creds = snowflake_creds(scope)
    creds.pop('sfPassword')

    with pytest.raises(AssertionError, match='Missing Snowflake credentials. Please configure any of .*'):
        dfds.load_credentials(ReconcileCredentialsConfig("databricks", creds))


def test_credentials_not_loaded_fails():
    engine, spark, ws, _ = initial_setup()
    data_source = SnowflakeDataSource(engine, spark, ws)

    # Call the get_schema method with predefined table, schema, and catalog names and assert that a PySparkException
    # is raised
    with pytest.raises(
        DataSourceRuntimeException,
        match=re.escape("Snowflake credentials have not been loaded. Please call load_credentials() first."),
    ):
        data_source.get_schema("org", "schema", "supplier")


@pytest.mark.skip("Turned off till we can handle case sensitivity.")
def test_normalize_identifier():
    engine, spark, ws, _ = initial_setup()
    data_source = SnowflakeDataSource(engine, spark, ws)

    assert data_source.normalize_identifier("a") == NormalizedIdentifier("`a`", '"a"')
    assert data_source.normalize_identifier('"b"') == NormalizedIdentifier("`b`", '"b"')
    assert data_source.normalize_identifier('"`e`f`"') == NormalizedIdentifier("```e``f```", '"`e`f`"')
    assert data_source.normalize_identifier('" g h "') == NormalizedIdentifier("` g h `", '" g h "')
    assert data_source.normalize_identifier('"""j""k"""') == NormalizedIdentifier('`"j"k"`', '"""j""k"""')
    assert data_source.normalize_identifier('"j""k"') == NormalizedIdentifier('`j"k`', '"j""k"')
