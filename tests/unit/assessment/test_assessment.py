from unittest.mock import patch

import yaml
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    ConfigureBigQueryAssessment,
    ConfigureRedshiftAssessment,
    ConfigureSqlServerAssessment,
    ConfigureSynapseAssessment,
    ConfigureTeradataAssessment,
    REDSHIFT_AUTH_TYPES,
)
from databricks.labs.lakebridge.connections.mssql_auth import AUTH_CHOICES

_AUTH_CHOICE_NAMES = [cls.__name__ for cls in AUTH_CHOICES]


def _auth_choice_index(class_name: str) -> int:
    """MockPrompts answers `prompts.choice` with the index into the choice list.

    The production code passes `sort=False`, so the order matches `AUTH_CHOICES` directly.
    """
    return _AUTH_CHOICE_NAMES.index(class_name)


def test_configure_sqlserver_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("SqlPassword"),
            r"Enter the database name": "TEST_TSQL_JDBC",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the username": "TEST_TSQL_USER",
            r"Enter the password": "TEST_TSQL_PASS",
            r"Trust server certificate": "no",
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
            'fetch_size': '4000',
            'login_timeout': 5,
            'password': 'TEST_TSQL_PASS',
            'port': 1433,
            'server': 'URL',
            'tz_info': 'UTC',
            'user': 'TEST_TSQL_USER',
            'trust_server_certificate': False,
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
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
            r"Trust server certificate": "yes",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fstream:
        credentials = yaml.safe_load(fstream)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryServicePrincipal"
    assert "user" not in credentials["mssql"]
    assert "password" not in credentials["mssql"]


def test_configure_sqlserver_credentials_ad_default(tmp_path):
    """ActiveDirectoryDefault: no user/password prompts; the driver resolves the identity at run time."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("ActiveDirectoryDefault"),
            r"Enter the database name": "TEST_DB",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
            r"Trust server certificate": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fstream:
        credentials = yaml.safe_load(fstream)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryDefault"
    assert "user" not in credentials["mssql"]
    assert "password" not in credentials["mssql"]


def test_configure_sqlserver_credentials_ad_password(tmp_path):
    """ActiveDirectoryPassword: prompts for username and password (same shape as SQL auth)."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("ActiveDirectoryPassword"),
            r"Enter the database name": "TEST_DB",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the username": "aad-user@example.com",
            r"Enter the password": "aad-pass",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "1000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 30,
            r"Trust server certificate": "yes",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    )
    assessment.run()

    with open(file, 'r', encoding='utf-8') as fstream:
        credentials = yaml.safe_load(fstream)

    assert credentials["mssql"]["auth_type"] == "ActiveDirectoryPassword"
    assert credentials["mssql"]["user"] == "aad-user@example.com"
    assert credentials["mssql"]["password"] == "aad-pass"


def test_configure_sqlserver_credentials_all_databases(tmp_path):
    """A blank mssql database is stored verbatim; downstream treats blank the same as the '*' sentinel."""
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Select authentication method": _auth_choice_index("SqlPassword"),
            r"Enter the database name": "*",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the username": "TEST_TSQL_USER",
            r"Enter the password": "TEST_TSQL_PASS",
            r"Trust server certificate": "no",
            r"Do you want to test the connection to mssql?.*": "no",
            r"Enter fetch size": "4000",
            r"Enter timezone.*": "UTC",
            r"Enter login timeout.*": 5,
        }
    )
    file = tmp_path / ".credentials.yml"
    ConfigureSqlServerAssessment(
        product_name="lakebridge", source_name="mssql", prompts=prompts, credential_file=file
    ).run()

    with open(file, 'r', encoding='utf-8') as handle:
        credentials = yaml.safe_load(handle)

    assert credentials['mssql']['database'] == "*"


def test_configure_synapse_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter Synapse workspace name": "test-workspace",
            r"Enter the username": "test-user",
            r"Enter the password": "test-password",
            r"Enter timezone \(e.g. America/New_York\)": "UTC",
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
                'development_endpoint': 'test-dev-endpoint',
                'auth_type': 'SqlPassword',
                'user': 'test-user',
                'password': 'test-password',
                'fetch_size': '1000',
                'login_timeout': '30',
                'tz_info': 'UTC',
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


def test_configure_bigquery_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("local"),
            r"Enter BigQuery project and region pairs.*": "customer-prod-1.us, customer-admin.eu",
            r"Enter lookback window in days to profile": "180",
            r"Enter max parallel SQLs per.*": "8",
            r"Exclude reservations and commitments data\?": "no",
            r"Exclude streaming and write API summary\?": "no",
            r"Do you want to test the connection to bigquery\?": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureBigQueryAssessment(
        product_name="lakebridge", source_name="bigquery", prompts=prompts, credential_file=file
    )
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'local',
        'secret_vault_name': None,
        'bigquery': {
            'pairs': [
                {'project': 'customer-prod-1', 'region': 'us'},
                {'project': 'customer-admin', 'region': 'eu'},
            ],
            'profiler': {
                'profiling_window_days': 180,
                'max_parallel_sqls': 8,
                'exclude_reservations_data': False,
                'exclude_streaming_metrics': False,
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

    with open(file, 'r', encoding='utf-8') as fstream:
        credentials = yaml.safe_load(fstream)

    workspace = credentials["synapse"]["workspace"]
    assert workspace["auth_type"] == "ActiveDirectoryServicePrincipal"
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

    # Test Teradata configurator
    teradata_configurator = create_assessment_configurator(
        source_system="teradata", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(teradata_configurator, ConfigureTeradataAssessment)

    # legacy_synapse (Azure Synapse dedicated SQL pool) reuses the SQL Server configurator
    legacy_synapse_configurator = create_assessment_configurator(
        source_system="legacy_synapse", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(legacy_synapse_configurator, ConfigureSqlServerAssessment)

    # Test BigQuery configurator
    bigquery_configurator = create_assessment_configurator(
        source_system="bigquery", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(bigquery_configurator, ConfigureBigQueryAssessment)

    redshift_configurator = create_assessment_configurator(
        source_system="redshift", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(redshift_configurator, ConfigureRedshiftAssessment)

    # Test invalid source system
    try:
        create_assessment_configurator(source_system="invalid", product_name="lakebridge", prompts=prompts)
        assert False, "Expected ValueError for invalid source system"
    except ValueError as e:
        assert str(e) == "Unsupported source system: invalid"


def test_configure_teradata_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter the Teradata server or host details": "TERADATA_HOST",
            r"Enter the port details": "1025",
            r"Enter the user details": "TERADATA_USER",
            r"Enter the environment variable name holding the password": "TERADATA_PASSWORD",
            r"Enter the default database name": "DBC",
            r"Do you want to test the connection to teradata\?": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    assessment = ConfigureTeradataAssessment(
        product_name="lakebridge", source_name="teradata", prompts=prompts, credential_file=file
    )
    assessment.run()

    expected_credentials = {
        'secret_vault_type': 'env',
        'secret_vault_name': None,
        'teradata': {
            'host': 'TERADATA_HOST',
            'port': 1025,
            'user': 'TERADATA_USER',
            # In env mode the stored value is the env var *name*, resolved by EnvGetter at runtime.
            'password': 'TERADATA_PASSWORD',
            'database': 'DBC',
        },
    }

    with open(file, 'r', encoding='utf-8') as handle:
        credentials = yaml.safe_load(handle)

    assert credentials == expected_credentials


def test_configure_redshift_credentials_sql_authentication(tmp_path):
    # ``MockPrompts.choice`` matches against the option *labels*, sorted; the answer here
    # is the index after sorting. ``sorted(["sql_authentication", "iam"])`` puts ``iam``
    # at index 0 and ``sql_authentication`` at index 1.
    prompts = MockPrompts(
        {
            r"Authentication type": sorted(["sql_authentication", "iam"]).index("sql_authentication"),
            r"Credential source \(local \| env \| file\)": sorted(["local", "env", "file"]).index("local"),
            r"Enter the Redshift cluster endpoint \(host\)": "redshift.example.com",
            r"Enter the port details": "5439",
            r"Enter the database name": "dev",
            r"Enter the user details": "test_user",
            r"Enter the password details": "test_password",
            r"Do you want to test the connection to redshift?.*": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    ConfigureRedshiftAssessment(
        product_name="lakebridge", source_name="redshift", prompts=prompts, credential_file=file
    ).run()

    with open(file, "r", encoding="utf-8") as f:
        credentials = yaml.safe_load(f)

    assert credentials == {
        "secret_vault_type": "local",
        "secret_vault_name": None,
        "redshift": {
            "auth_type": "sql_authentication",
            "ssl": "yes",
            "host": "redshift.example.com",
            "port": 5439,
            "database": "dev",
            "user": "test_user",
            "password": "test_password",
        },
    }


def test_configure_redshift_credentials_iam(tmp_path):
    prompts = MockPrompts(
        {
            r"Authentication type": sorted(["sql_authentication", "iam"]).index("iam"),
            r"Credential source \(local \| env \| file\)": sorted(["local", "env", "file"]).index("local"),
            r"Enter the Redshift cluster endpoint \(host\)": "redshift.example.com",
            r"Enter the port details": "5439",
            r"Enter the database name": "dev",
            r"DB user to assume via GetClusterCredentials.*": "awsuser",
            r"Cluster identifier.*": "my-cluster",
            r"AWS profile name.*": "default",
            r"AWS region.*": "us-west-2",
            r"Do you want to test the connection to redshift?.*": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    ConfigureRedshiftAssessment(
        product_name="lakebridge", source_name="redshift", prompts=prompts, credential_file=file
    ).run()

    with open(file, "r", encoding="utf-8") as f:
        credentials = yaml.safe_load(f)

    redshift_creds = credentials["redshift"]
    # IAM path must not write user/password — the connector resolves AWS identity instead.
    assert "user" not in redshift_creds
    assert "password" not in redshift_creds
    assert redshift_creds == {
        "auth_type": "iam",
        "ssl": "yes",
        "host": "redshift.example.com",
        "port": 5439,
        "database": "dev",
        "db_user": "awsuser",
        "cluster_identifier": "my-cluster",
        "aws_profile": "default",
        "region": "us-west-2",
    }


def test_configure_redshift_credentials_iam_optional_fields_skipped(tmp_path):
    """Empty answers to optional IAM fields must not write the key (no '' poisoning)."""
    prompts = MockPrompts(
        {
            r"Authentication type": sorted(["sql_authentication", "iam"]).index("iam"),
            r"Credential source \(local \| env \| file\)": sorted(["local", "env", "file"]).index("local"),
            r"Enter the Redshift cluster endpoint \(host\)": "redshift.example.com",
            r"Enter the port details": "5439",
            r"Enter the database name": "dev",
            r"DB user to assume via GetClusterCredentials.*": "",
            r"Cluster identifier.*": "",
            r"AWS profile name.*": "",
            r"AWS region.*": "",
            r"Do you want to test the connection to redshift?.*": "no",
        }
    )
    file = tmp_path / ".credentials.yml"
    ConfigureRedshiftAssessment(
        product_name="lakebridge", source_name="redshift", prompts=prompts, credential_file=file
    ).run()

    with open(file, "r", encoding="utf-8") as f:
        credentials = yaml.safe_load(f)

    assert credentials["redshift"] == {
        "auth_type": "iam",
        "ssl": "yes",
        "host": "redshift.example.com",
        "port": 5439,
        "database": "dev",
    }


def test_redshift_configurator_writes_only_connector_supported_auth_types():
    """Regression guard for the configurator/connector contract.

    Any auth_type the configurator can write MUST be a value the connector knows how to
    handle (see ``RedshiftConnector._connect``). This catches drift before users hit
    runtime ``ConnectionError`` after configuring credentials.
    """
    connector_supported = {"sql_authentication", "iam"}
    assert set(REDSHIFT_AUTH_TYPES) <= connector_supported, (
        f"Configurator offers auth_type(s) {set(REDSHIFT_AUTH_TYPES) - connector_supported} "
        f"that RedshiftConnector._connect does not implement."
    )


def test_test_connection_default_uses_database_manager():
    """Sources without an override go through DatabaseManager (the JDBC connector)."""
    configurator = ConfigureRedshiftAssessment(
        product_name="lakebridge", source_name="redshift", prompts=MockPrompts({})
    )
    raw_config = {"host": "redshift.example.com", "database": "dev"}
    with (
        patch("databricks.labs.lakebridge.assessments.configure_assessment.create_credential_manager") as cred_manager,
        patch("databricks.labs.lakebridge.assessments.configure_assessment.DatabaseManager") as database_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = raw_config
        database_manager.return_value.__enter__.return_value.check_connection.return_value = True
        configurator.test_connection()
    database_manager.assert_called_once_with("redshift", raw_config)


def test_synapse_test_connection_delegates_to_pools():
    """Synapse overrides the check to validate each SQL pool instead of one connection."""
    configurator = ConfigureSynapseAssessment(product_name="lakebridge", source_name="synapse", prompts=MockPrompts({}))
    raw_config = {"workspace": {"name": "ws"}}
    with (
        patch("databricks.labs.lakebridge.assessments.configure_assessment.create_credential_manager") as cred_manager,
        patch("databricks.labs.lakebridge.assessments.configure_assessment.validate_synapse_pools") as validate,
    ):
        cred_manager.return_value.get_credentials.return_value = raw_config
        configurator.test_connection()
    validate.assert_called_once_with(raw_config)


def test_bigquery_test_connection_delegates_to_pairs():
    """BigQuery overrides the check to probe each (project, region) pair."""
    configurator = ConfigureBigQueryAssessment(
        product_name="lakebridge", source_name="bigquery", prompts=MockPrompts({})
    )
    raw_config = {"pairs": [{"project": "p", "region": "us"}]}
    with (
        patch("databricks.labs.lakebridge.assessments.configure_assessment.create_credential_manager") as cred_manager,
        patch("databricks.labs.lakebridge.assessments.configure_assessment.validate_bigquery_pairs") as validate,
    ):
        cred_manager.return_value.get_credentials.return_value = raw_config
        configurator.test_connection()
    validate.assert_called_once_with(raw_config)
