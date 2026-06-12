import pytest
import yaml
from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.assessments.configure_assessment import (
    create_assessment_configurator,
    ConfigureBigQueryAssessment,
    ConfigureRedshiftAssessment,
    ConfigureSqlServerAssessment,
    ConfigureSynapseAssessment,
    REDSHIFT_AUTH_TYPES,
)


def test_configure_sqlserver_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter the database name": "TEST_TSQL_JDBC",
            r"Enter the ODBC driver installed locally.*": "ODBC Driver 18 for SQL Server",
            r"Enter the fully-qualified server name": "URL",
            r"Enter the port details": "1433",
            r"Enter the SQL username": "TEST_TSQL_USER",
            r"Enter the SQL password": "TEST_TSQL_PASS",
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
            'auth_type': 'sql_authentication',
            'database': 'TEST_TSQL_JDBC',
            'driver': 'ODBC Driver 18 for SQL Server',
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


def test_configure_synapse_credentials(tmp_path):
    prompts = MockPrompts(
        {
            r"Enter secret vault type \(local \| env\)": sorted(['local', 'env']).index("env"),
            r"Enter Synapse workspace name": "test-workspace",
            r"Enter SQL user": "test-user",
            r"Enter SQL password": "test-password",
            r"Enter timezone \(e.g. America/New_York\)": "UTC",
            r"Enter the ODBC driver installed locally": "ODBC Driver 18 for SQL Server",
            r"Enter development endpoint": "test-dev-endpoint",
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
                'sql_user': 'test-user',
                'sql_password': 'test-password',
                'tz_info': 'UTC',
                'driver': 'ODBC Driver 18 for SQL Server',
            },
            'azure_api_access': {
                'development_endpoint': 'test-dev-endpoint',
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

    # Test BigQuery configurator
    bigquery_configurator = create_assessment_configurator(
        source_system="bigquery", product_name="lakebridge", prompts=prompts
    )
    assert isinstance(bigquery_configurator, ConfigureBigQueryAssessment)

    # Test invalid source system
    try:
        create_assessment_configurator(source_system="invalid", product_name="lakebridge", prompts=prompts)
        assert False, "Expected ValueError for invalid source system"
    except ValueError as e:
        assert str(e) == "Unsupported source system: invalid"


@pytest.mark.parametrize("variant", ["redshift_serverless", "redshift_provisioned", "redshift_provisioned_multi_az"])
def test_create_assessment_configurator_redshift_variants(variant):
    prompts = MockPrompts({})
    configurator = create_assessment_configurator(source_system=variant, product_name="lakebridge", prompts=prompts)
    assert isinstance(configurator, ConfigureRedshiftAssessment)
    # All three variants share the "redshift" credentials key (verified end-to-end via
    # vars() to avoid touching the protected attribute syntactically).
    assert vars(configurator)["_source_name"] == "redshift"


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
