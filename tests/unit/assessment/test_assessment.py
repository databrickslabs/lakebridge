import yaml
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    ConfigureSqlServerAssessment,
    ConfigureSynapseAssessment,
)


def test_configure_sqlserver_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter the database name": "TEST_TSQL_JDBC",
            r"Enter the driver details": "ODBC Driver 18 for SQL Server",
            r"Enter the server or host details": "TEST_TSQL_JDBC",
            r"Enter the port details": "1433",
            r"Enter the user details": "TEST_TSQL_USER",
            r"Enter the password details": "TEST_TSQL_PASS",
            r"Do you want to test the connection to mssql?": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(product_name="lakebridge", prompts=prompts, credential_file=file)
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'env',
        'secret_vault_name': None,
        'mssql': {
            'database': 'TEST_TSQL_JDBC',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'TEST_TSQL_JDBC',
            'port': 1433,
            'user': 'TEST_TSQL_USER',
            'password': 'TEST_TSQL_PASS',
        },
    }

    with open(file, 'r', encoding='utf-8') as file:
        credentials = yaml.safe_load(file)

    assert credentials == expected_credentials


def test_configure_synapse_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter Synapse workspace name": "test-workspace",
            r"Enter dedicated SQL endpoint": "test-dedicated-endpoint",
            r"Enter serverless SQL endpoint": "test-serverless-endpoint",
            r"Enter SQL user": "test-user",
            r"Enter SQL password": "test-password",
            r"Enter timezone \(e.g. America/New_York\)": "UTC",
            r"Enter development endpoint": "test-dev-endpoint",
            r"Enter Azure client ID": "test-client-id",
            r"Enter Azure tenant ID": "test-tenant-id",
            r"Enter Azure client secret": "test-client-secret",
            r"Select authentication type": sorted(
                ["sql_authentication", "ad_passwd_authentication", "spn_authentication"]
            ).index("sql_authentication"),
            r"Enter fetch size": "1000",
            r"Enter login timeout \(seconds\)": "30",
            r"Exclude serverless SQL pool from profiling\?": "no",
            r"Exclude dedicated SQL pools from profiling\?": "no",
            r"Exclude Spark pools from profiling\?": "no",
            r"Exclude monitoring metrics from profiling\?": "no",
            r"Redact SQL pools SQL text\?": "no",
            r"Do you want to test the connection to synapse?": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSynapseAssessment(product_name="lakebridge", prompts=prompts, credential_file=file)
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'env',
        'secret_vault_name': None,
        'synapse': {
            'workspace': {
                'name': 'test-workspace',
                'dedicated_sql_endpoint': 'test-dedicated-endpoint',
                'serverless_sql_endpoint': 'test-serverless-endpoint',
                'sql_user': 'test-user',
                'sql_password': 'test-password',
                'tz_info': 'UTC',
            },
            'azure_api_access': {
                'development_endpoint': 'test-dev-endpoint',
                'azure_client_id': 'test-client-id',
                'azure_tenant_id': 'test-tenant-id',
                'azure_client_secret': 'test-client-secret',
            },
            'jdbc': {
                'auth_type': 'sql_authentication',
                'fetch_size': '1000',
                'login_timeout': '30',
            },
            'profiler': {
                'exclude_serverless_sql_pool': False,
                'exclude_dedicated_sql_pools': False,
                'exclude_spark_pools': False,
                'exclude_monitoring_metrics': False,
                'redact_sql_pools_sql_text': False,
            },
        },
    }

    with open(file, 'r', encoding='utf-8') as file:
        credentials = yaml.safe_load(file)

    assert credentials == expected_credentials


def test_create_assessment_configurator():
    prompts = MockPrompts({})

    # Test SQL Server configurator
    sql_server_configurator = create_assessment_configurator(
        source_system="mssql", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(sql_server_configurator, ConfigureSqlServerAssessment)

    # Test Synapse configurator
    synapse_configurator = create_assessment_configurator(
        source_system="synapse", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(synapse_configurator, ConfigureSynapseAssessment)

    # Test invalid source system
    try:
        create_assessment_configurator(source_system="invalid", product_name="lakebridge", prompts=prompts)
        assert False, "Expected ValueError for invalid source system"
    except ValueError as e:
        assert str(e) == "Unsupported source system: invalid"
