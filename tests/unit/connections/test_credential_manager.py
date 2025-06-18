import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
import os

product_name = "remorph"


@pytest.fixture
def env_getter():
    return MagicMock(spec=EnvGetter)


@pytest.fixture
def local_credentials():
    return {
        'secret_vault_type': 'local',
        'mssql': {
            'database': 'DB_NAME',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'example_host',
            'user': 'local_user',
            'password': 'local_password',
        },
    }


@pytest.fixture
def env_credentials():
    return {
        'secret_vault_type': 'env',
        'mssql': {
            'database': 'DB_NAME',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'example_host',
            'user': 'MSSQL_USER_ENV',
            'password': 'MSSQL_PASSWORD_ENV',
        },
    }


@pytest.fixture
def databricks_credentials():
    return {
        'secret_vault_type': 'databricks',
        'secret_vault_name': 'databricks_vault_name',
        'mssql': {
            'database': 'DB_NAME',
            'driver': 'ODBC Driver 18 for SQL Server',
            'server': 'example_host',
            'user': 'databricks_user',
            'password': 'databricks_password',
        },
    }


@patch('databricks.labs.lakebridge.connections.credential_manager._load_credentials')
@patch('databricks.labs.lakebridge.connections.credential_manager._get_home')
def test_local_credentials(mock_get_home, mock_load_credentials, local_credentials, env_getter):
    mock_load_credentials.return_value = local_credentials
    mock_get_home.return_value = Path("/fake/home")
    credentials = create_credential_manager(product_name, env_getter)
    creds = credentials.get_credentials('mssql')
    assert creds['user'] == 'local_user'
    assert creds['password'] == 'local_password'


@patch('databricks.labs.lakebridge.connections.credential_manager._load_credentials')
@patch('databricks.labs.lakebridge.connections.credential_manager._get_home')
@patch.dict('os.environ', {'MSSQL_USER_ENV': 'env_user', 'MSSQL_PASSWORD_ENV': 'env_password'})
def test_env_credentials(mock_get_home, mock_load_credentials, env_credentials, env_getter):
    mock_load_credentials.return_value = env_credentials
    mock_get_home.return_value = Path("/fake/home")
    env_getter.get.side_effect = lambda key: os.environ[key]
    credentials = create_credential_manager(product_name, env_getter)
    creds = credentials.get_credentials('mssql')
    assert creds['user'] == 'env_user'
    assert creds['password'] == 'env_password'


@patch('databricks.labs.lakebridge.connections.credential_manager._load_credentials')
@patch('databricks.labs.lakebridge.connections.credential_manager._get_home')
def test_databricks_credentials(mock_get_home, mock_load_credentials, databricks_credentials, env_getter):
    mock_load_credentials.return_value = databricks_credentials
    mock_get_home.return_value = Path("/fake/home")
    credentials = create_credential_manager(product_name, env_getter)
    with pytest.raises(NotImplementedError):
        credentials.get_credentials('mssql')
