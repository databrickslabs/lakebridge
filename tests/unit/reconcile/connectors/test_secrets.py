import base64
from unittest.mock import create_autospec

import pytest

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.workspace import GetSecretResponse

from databricks.labs.lakebridge.connections.credential_manager import DatabricksSecretProvider


def mock_secret(scope, key):
    secret_mock = {
        "some_scope": {
            'user_name': GetSecretResponse(
                key='user_name', value=base64.b64encode(bytes('my_user', 'utf-8')).decode('utf-8')
            ),
            'password': GetSecretResponse(
                key='password', value=base64.b64encode(bytes('my_password', 'utf-8')).decode('utf-8')
            ),
        }
    }

    return secret_mock.get(scope).get(key)


def test_get_secrets_happy():
    ws = create_autospec(WorkspaceClient)
    ws.secrets.get_secret.side_effect = mock_secret

    sut = DatabricksSecretProvider(ws)

    assert sut.get_secret("some_scope/user_name") == "my_user"
    assert sut.get_secret_or_none("some_scope/user_name") == "my_user"
    assert sut.get_secret("some_scope/password") == "my_password"
    assert sut.get_secret_or_none("some_scope/password") == "my_password"


def test_get_secrets_not_found_exception():
    ws = create_autospec(WorkspaceClient)
    ws.secrets.get_secret.side_effect = NotFound("Test Exception")
    sut = DatabricksSecretProvider(ws)

    with pytest.raises(
        KeyError, match="Secret does not exist with scope: some_scope and key: unknown : Test Exception"
    ):
        sut.get_secret("some_scope/unknown")


def test_get_secrets_not_found_swallow():
    ws = create_autospec(WorkspaceClient)
    ws.secrets.get_secret.side_effect = NotFound("Test Exception")
    sut = DatabricksSecretProvider(ws)

    assert sut.get_secret_or_none("some_scope/unknown") is None


def test_get_secrets_invalid_name():
    ws = create_autospec(WorkspaceClient)
    sut = DatabricksSecretProvider(ws)

    with pytest.raises(AssertionError, match="Secret name must be in the format 'scope/secret'"):
        sut.get_secret("just_key")

    with pytest.raises(AssertionError, match="Secret name must be in the format 'scope/secret'"):
        sut.get_secret_or_none("just_key")
