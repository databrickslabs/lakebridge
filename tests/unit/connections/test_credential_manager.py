import os

import pytest

from databricks.labs.lakebridge.connections.credential_manager import (
    CredentialManager,
    LocalSecretProvider,
    EnvSecretProvider,
    DatabricksSecretProvider,
)
from databricks.labs.lakebridge.connections.env_getter import EnvGetter


@pytest.fixture(scope="session", autouse=True)
def setup_test_env_vars():
    # Set all test environment variables
    test_vars = {
        'MSSQL_USER_ENV': 'env_user',
        'MSSQL_PASSWORD_ENV': 'env_password',
        'SYNAPSE_USER_ENV': 'resolved_user',
        'SYNAPSE_PASSWORD_ENV': 'resolved_password',
        'POOL1_ENV': 'pool1',
        'POOL2_ENV': 'pool2',
        'ENV_VAR_IN_LIST': 'resolved_from_list',
    }

    for key, value in test_vars.items():
        os.environ[key] = value

    yield  # Tests run here

    # Cleanup after all tests complete
    for key in test_vars:
        os.environ.pop(key, None)


def _create_secret_providers() -> dict:
    """Create real secret providers for testing."""
    return {
        'local': LocalSecretProvider(),
        'env': EnvSecretProvider(EnvGetter()),
        'databricks': DatabricksSecretProvider(),
    }


def test_local_credentials_flat_structure():
    """Test flat MSSQL structure with local secret provider ."""
    credentials = {
        'secret_vault_type': 'local',
        'mssql': {
            'database': 'DB_NAME',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'example_host',
            'user': 'local_user',
            'password': 'local_password',
            'port': 1433,
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('mssql')

    assert creds['user'] == 'local_user'
    assert creds['password'] == 'local_password'
    assert creds['database'] == 'DB_NAME'
    assert creds['port'] == 1433


def test_env_credentials_with_real_env_getter():
    """Test env secret provider with real EnvGetter - uses session env vars."""
    credentials = {
        'secret_vault_type': 'env',
        'mssql': {
            'database': 'DB_NAME',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'example_host',
            'user': 'MSSQL_USER_ENV',
            'password': 'MSSQL_PASSWORD_ENV',
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('mssql')

    assert creds['user'] == 'env_user'
    assert creds['password'] == 'env_password'
    assert creds['database'] == 'DB_NAME'


def test_env_credentials_fallback_to_literal_value():
    """Test that env provider falls back to literal value if env var not found."""
    credentials = {
        'secret_vault_type': 'env',
        'mssql': {
            'user': 'NONEXISTENT_ENV_VAR',
            'database': 'DB_NAME',
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('mssql')

    assert creds['user'] == 'NONEXISTENT_ENV_VAR'
    assert creds['database'] == 'DB_NAME'


def test_databricks_credentials_raises_not_implemented():
    """Test that databricks secret provider raises NotImplementedError ."""
    credentials = {
        'secret_vault_type': 'databricks',
        'secret_vault_name': 'databricks_vault_name',
        'mssql': {
            'database': 'DB_NAME',
            'user': 'databricks_user',
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())

    with pytest.raises(NotImplementedError, match="Databricks secret vault not implemented"):
        manager.get_credentials('mssql')


def test_nested_dict_credentials_local_vault():
    """Test Synapse-style nested dictionaries with local secret vault.

    Validates handling of complex nested credential structures required by
    enterprise data sources. The test structure mirrors actual Synapse credential
    requirements with multiple configuration levels:

    - workspace: SQL connection details (endpoints, user, password, driver, port)
    - azure_api_access: Azure API endpoints for resource management
    - jdbc: JDBC-specific settings (authentication, timeouts, fetch size)
    - profiler: Profiler configuration (pool exclusions, profiling lists)

    This demonstrates the credential manager's ability to:
    1. Preserve nested structure without flattening
    2. Maintain type information through recursive processing
    3. Handle multiple configuration domains in a single credential entry
    """
    credentials = {
        'secret_vault_type': 'local',
        'synapse': {
            'workspace': {
                'name': 'test-workspace',
                'dedicated_sql_endpoint': 'test-workspace.sql.azuresynapse.net',
                'serverless_sql_endpoint': 'test-workspace-ondemand.sql.azuresynapse.net',
                'sql_user': 'synapse_user',
                'sql_password': 'synapse_password',
                'tz_info': 'UTC',
                'driver': 'ODBC Driver 18 for SQL Server',
                'port': 1433,
            },
            'azure_api_access': {
                'development_endpoint': 'https://test-dev-endpoint.azuresynapse.net',
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
                'dedicated_sql_pools_profiling_list': None,
                'spark_pools_profiling_list': None,
            },
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('synapse')

    # Verify nested structure is preserved
    assert 'workspace' in creds
    assert 'azure_api_access' in creds
    assert 'jdbc' in creds
    assert 'profiler' in creds

    # Verify strings are returned as-is with local vault
    assert creds['workspace']['name'] == 'test-workspace'
    assert creds['workspace']['sql_user'] == 'synapse_user'
    assert creds['workspace']['sql_password'] == 'synapse_password'

    # Verify integers are preserved
    assert creds['workspace']['port'] == 1433

    # Verify booleans are preserved
    assert creds['profiler']['exclude_serverless_sql_pool'] is False

    # Verify None values are preserved
    assert creds['profiler']['dedicated_sql_pools_profiling_list'] is None


def test_nested_dict_credentials_env_vault():
    """Test Synapse nested dictionaries with env secret vault - uses session env vars."""
    credentials = {
        'secret_vault_type': 'env',
        'synapse': {
            'workspace': {
                'name': 'test-workspace',
                'sql_user': 'SYNAPSE_USER_ENV',
                'sql_password': 'SYNAPSE_PASSWORD_ENV',
                'port': 1433,
            },
            'profiler': {
                'exclude_spark_pools': False,
                'spark_pools_profiling_list': ['POOL1_ENV', 'POOL2_ENV'],
            },
        },
    }

    # Use real EnvGetter !
    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('synapse')

    # Verify nested structure is preserved
    assert 'workspace' in creds
    assert 'profiler' in creds

    # Verify strings are resolved from environment variables
    assert creds['workspace']['sql_user'] == 'resolved_user'
    assert creds['workspace']['sql_password'] == 'resolved_password'

    # Verify integers are preserved (not treated as secrets)
    assert creds['workspace']['port'] == 1433

    # Verify booleans are preserved (not treated as secrets)
    assert creds['profiler']['exclude_spark_pools'] is False

    # Verify lists with strings are processed recursively
    assert creds['profiler']['spark_pools_profiling_list'] == ['pool1', 'pool2']


def test_mixed_data_types_preserved():
    """Test all data types (str, int, bool, float, None, list, dict) preserved ."""
    credentials = {
        'secret_vault_type': 'local',
        'test_source': {
            'string_value': 'test_string',
            'int_value': 42,
            'float_value': 3.14,
            'bool_true': True,
            'bool_false': False,
            'none_value': None,
            'list_value': ['item1', 'item2', 123],
            'nested_dict': {
                'nested_string': 'nested_value',
                'nested_int': 100,
            },
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('test_source')

    # Verify each data type is preserved correctly
    assert creds['string_value'] == 'test_string'
    assert creds['int_value'] == 42
    assert creds['float_value'] == 3.14
    assert creds['bool_true'] is True
    assert creds['bool_false'] is False
    assert creds['none_value'] is None
    assert creds['list_value'] == ['item1', 'item2', 123]
    assert creds['nested_dict']['nested_string'] == 'nested_value'
    assert creds['nested_dict']['nested_int'] == 100


def test_deeply_nested_credentials():
    """Test deeply nested credential structures (4+ levels) ."""
    credentials = {
        'secret_vault_type': 'local',
        'test_source': {
            'level1': {
                'level2': {
                    'level3': {
                        'level4': {
                            'deeply_nested_value': 'found_it',
                            'deeply_nested_int': 999,
                        }
                    }
                }
            }
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('test_source')

    # Verify deep nesting is preserved
    assert creds['level1']['level2']['level3']['level4']['deeply_nested_value'] == 'found_it'
    assert creds['level1']['level2']['level3']['level4']['deeply_nested_int'] == 999


def test_empty_nested_dict_and_list():
    """Test handling of empty dictionaries and lists ."""
    credentials = {
        'secret_vault_type': 'local',
        'test_source': {
            'empty_dict': {},
            'empty_list': [],
            'dict_with_empty_values': {
                'nested_empty_dict': {},
                'nested_empty_list': [],
            },
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('test_source')

    # Verify empty structures are preserved
    assert creds['empty_dict'] == {}
    assert creds['empty_list'] == []
    assert creds['dict_with_empty_values']['nested_empty_dict'] == {}
    assert creds['dict_with_empty_values']['nested_empty_list'] == []


def test_list_with_env_vars():
    """Test that strings inside lists are resolved from env vars - uses session env vars."""
    credentials = {
        'secret_vault_type': 'env',
        'test_source': {
            'string_list': ['literal_value', 'ENV_VAR_IN_LIST'],
            'mixed_list': ['string', 123, True, None],
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())
    creds = manager.get_credentials('test_source')

    # Verify strings in lists are resolved from env
    assert creds['string_list'] == ['literal_value', 'resolved_from_list']

    # Verify mixed types in lists are preserved correctly
    assert creds['mixed_list'] == ['string', 123, True, None]
    assert isinstance(creds['mixed_list'][1], int)
    assert isinstance(creds['mixed_list'][2], bool)


def test_unsupported_secret_vault_type_raises_error():
    """Test that unsupported secret vault type raises ValueError ."""
    credentials = {
        'secret_vault_type': 'unsupported_vault',
        'mssql': {
            'user': 'test_user',
        },
    }

    with pytest.raises(ValueError, match="Unsupported secret vault type: unsupported_vault"):
        CredentialManager(credentials, _create_secret_providers())


def test_source_not_found_raises_error():
    """Test that requesting non-existent source raises KeyError ."""
    credentials = {
        'secret_vault_type': 'local',
        'mssql': {
            'user': 'test_user',
        },
    }

    manager = CredentialManager(credentials, _create_secret_providers())

    with pytest.raises(KeyError, match="Source system: oracle credentials not found"):
        manager.get_credentials('oracle')


def test_invalid_credential_format_raises_error():
    """Test that non-dict credential value raises KeyError ."""
    credentials = {
        'secret_vault_type': 'local',
        'mssql': 'not_a_dict',  # Invalid format
    }

    manager = CredentialManager(credentials, _create_secret_providers())

    with pytest.raises(KeyError, match="Invalid credential format for source: mssql"):
        manager.get_credentials('mssql')
