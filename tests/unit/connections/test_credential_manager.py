import base64
from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import GetSecretResponse


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
        'mssql': {
            'database': 'databricks_vault_name/db_key',
            'server': 'databricks_vault_name/host_key',
            'user': 'databricks_vault_name/user_key',
            'password': 'databricks_vault_name/pass_key',
        },
    }


@pytest.fixture
def databricks_invalid_key():
    return {
        'secret_vault_type': 'databricks',
        'mssql': {
            'database': 'without_scope',
        },
    }


def test_local_credentials(local_credentials: dict[str, str]) -> None:
    credentials = create_credential_manager(local_credentials)
    creds = credentials.get_credentials('mssql')
    assert creds['user'] == 'local_user'
    assert creds['password'] == 'local_password'


@patch.dict('os.environ', {'MSSQL_USER_ENV': 'env_user', 'MSSQL_PASSWORD_ENV': 'env_password'})
def test_env_credentials(env_credentials: dict[str, str]) -> None:
    credentials = create_credential_manager(env_credentials)
    creds = credentials.get_credentials('mssql')
    assert creds['user'] == 'env_user'
    assert creds['password'] == 'env_password'


def test_databricks_credentials(databricks_credentials: dict[str, str], mock_workspace_client) -> None:
    mock_workspace_client.secrets.get_secret.return_value = GetSecretResponse(
        key='some_key', value=base64.b64encode(bytes('some_secret', 'utf-8')).decode('utf-8')
    )
    credentials = create_credential_manager(databricks_credentials, mock_workspace_client)
    creds = credentials.get_credentials('mssql')
    assert creds['user'] == 'some_secret'
    assert creds['password'] == 'some_secret'


def test_databricks_credentials_not_found(databricks_credentials: dict[str, str], mock_workspace_client) -> None:
    mock_workspace_client.secrets.get_secret.side_effect = NotFound("Test Exception")
    credentials = create_credential_manager(databricks_credentials, mock_workspace_client)

    with pytest.raises(KeyError, match="Secret does not exist with scope: databricks_vault_name and key: db_key"):
        credentials.get_credentials("mssql")


def test_databricks_invalid_key(databricks_invalid_key: dict[str, str], mock_workspace_client) -> None:
    credentials = create_credential_manager(databricks_invalid_key, mock_workspace_client)

    with pytest.raises(ValueError, match="Secret key must be in the format 'scope/secret': Got without_scope"):
        credentials.get_credentials("mssql")
