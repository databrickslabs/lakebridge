import yaml
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    ConfigureSqlServerAssessment,
    ConfigureSynapseAssessment,
)
from databricks.labs.lakebridge.connections.mssql_auth import AUTH_CHOICES


_AUTH_CHOICE_NAMES = sorted(cls.__name__ for cls in AUTH_CHOICES)


def _auth_choice_index(class_name: str) -> int:
    """MockPrompts answers `prompts.choice` with the index into the alphabetically-sorted choice list."""
    return _AUTH_CHOICE_NAMES.index(class_name)


def test_configure_sqlserver_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("SqlPassword"),
            r"Enter the database name": "TEST_TSQL_JDBC",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the username": "TEST_TSQL_USER",
            r"Enter the password": "TEST_TSQL_PASS",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "4000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 5,
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'env',
        'secret_vault_name': None,
        'mssql': {
            'auth_type': 'SqlPassword',
            'database': 'TEST_TSQL_JDBC',
            'driver': 'ODBC Driver 18 for SQL Server',
            'fetch_size': '4000',
            'login_timeout': 5,
            'password': 'TEST_TSQL_PASS',
            'port': 1433,
            'server': 'URL',
            'tz_info': 'UTC',
            'user': 'TEST_TSQL_USER',
        },
    }

    with open(file, 'r', encoding='utf-8') as file:
        credentials = yaml.safe_load(file)

    assert credentials == expected_credentials


def test_configure_sqlserver_credentials_spn(tmp_path):
    """ActiveDirectoryServicePrincipal: user/password are NOT prompted; credentials come from env vars at run time."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("ActiveDirectoryServicePrincipal"),
            r"Enter the database name": "TEST_DB",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fh:
        credentials = yaml.safe_load(fh)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryServicePrincipal"
    assert "user" not in credentials["mssql"]
    assert "password" not in credentials["mssql"]


def test_configure_sqlserver_credentials_ad_password(tmp_path):
    """ActiveDirectoryPassword: prompts for username and password (same shape as SQL auth)."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("ActiveDirectoryPassword"),
            r"Enter the database name": "TEST_DB",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the username": "aad-user@example.com",
            r"Enter the password": "aad-pass",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fh:
        credentials = yaml.safe_load(fh)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryPassword"
    assert credentials["mssql"]["user"] == "aad-user@example.com"
    assert credentials["mssql"]["password"] == "aad-pass"


def test_configure_sqlserver_credentials_interactive_skips_password(tmp_path):
    """ActiveDirectoryInteractive: user is optional (pre-fill), password is never prompted."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("ActiveDirectoryInteractive"),
            r"Enter the database name": "TEST_DB",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the AAD username.*": "interactive-user@example.com",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fh:
        credentials = yaml.safe_load(fh)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryInteractive"
    assert credentials["mssql"]["user"] == "interactive-user@example.com"
    assert "password" not in credentials["mssql"]


def test_configure_synapse_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter Synapse workspace name": "test-workspace",
            r"Enter the username": "test-user",
            r"Enter the password": "test-password",
            r"Enter timezone \(e.g. America/New_York\)": "UTC",
            r"Enter the ODBC driver installed locally": "ODBC Driver 18 for SQL Server",
            r"Enter development endpoint": "test-dev-endpoint",
            r"Select authentication method": _auth_choice_index("SqlPassword"),
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
    assessment = ConfigureSynapseAssessment(
        product_name="lakebridge", source_name="synapse", prompts=prompts, credential_file=file
    )
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'env',
        'secret_vault_name': None,
        'synapse': {
            'workspace': {
                'name': 'test-workspace',
                'dedicated_sql_endpoint': 'test-workspace.sql.azuresynapse.net',
                'serverless_sql_endpoint': 'test-workspace-ondemand.sql.azuresynapse.net',
                'user': 'test-user',
                'password': 'test-password',
                'tz_info': 'UTC',
                'driver': 'ODBC Driver 18 for SQL Server',
            },
            'azure_api_access': {
                'development_endpoint': 'test-dev-endpoint',
            },
            'jdbc': {
                'auth_type': 'SqlPassword',
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


def test_configure_synapse_credentials_spn(tmp_path):
    """Synapse SPN: workspace omits user/password; auth_type is the new ODBC class name."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter Synapse workspace name": "test-workspace",
            r"Enter timezone \(e.g. America/New_York\)": "UTC",
            r"Enter the ODBC driver installed locally": "ODBC Driver 18 for SQL Server",
            r"Enter development endpoint": "test-dev-endpoint",
            r"Select authentication method": _auth_choice_index("ActiveDirectoryServicePrincipal"),
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
    assessment = ConfigureSynapseAssessment(
        product_name="lakebridge", source_name="synapse", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fh:
        credentials = yaml.safe_load(fh)

    workspace = credentials["synapse"]["workspace"]
    assert credentials["synapse"]["jdbc"]["auth_type"] == "ActiveDirectoryServicePrincipal"
    assert "user" not in workspace
    assert "password" not in workspace


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

    # legacy_synapse (Azure Synapse dedicated SQL pool) reuses the SQL Server configurator
    legacy_synapse_configurator = create_assessment_configurator(
        source_system="legacy_synapse", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(legacy_synapse_configurator, ConfigureSqlServerAssessment)

    # Test invalid source system
    try:
        create_assessment_configurator(source_system="invalid", product_name="lakebridge", prompts=prompts)
        assert False, "Expected ValueError for invalid source system"
    except ValueError as e:
        assert str(e) == "Unsupported source system: invalid"
