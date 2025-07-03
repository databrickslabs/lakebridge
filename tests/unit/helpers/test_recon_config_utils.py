from unittest.mock import patch

import pytest

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts
from databricks.sdk.errors.platform import ResourceDoesNotExist
from databricks.sdk.service.workspace import SecretScope

SOURCE_DICT = {"databricks": "0", "oracle": "1", "snowflake": "2"}
SCOPE_NAME = "dummy_scope"


def test_configure_secrets_snowflake_overwrite(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Select the source": SOURCE_DICT["snowflake"],
            r"Enter Secret Scope name": SCOPE_NAME,
            r"Enter Snowflake URL": "dummy",
            r"Enter Account Name": "dummy",
            r"Enter User": "dummy",
            r"Enter Password": "dummy",
            r"Enter Database": "dummy",
            r"Enter Schema": "dummy",
            r"Enter Snowflake Warehouse": "dummy",
            r"Enter Role": "dummy",
            r"Do you want to overwrite.*": "yes",
        }
    )
    mock_workspace_client.secrets.list_scopes.side_effect = [[SecretScope(name=SCOPE_NAME)]]
    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    recon_conf.prompt_and_save_connection_details()


def test_configure_secrets_oracle_insert(mock_workspace_client):
    # mock prompts for Oracle
    prompts = MockPrompts(
        {
            r"Select the source": SOURCE_DICT["oracle"],
            r"Enter Secret Scope name": SCOPE_NAME,
            r"Do you want to create a new one?": "yes",
            r"Enter User": "dummy",
            r"Enter Password": "dummy",
            r"Enter host": "dummy",
            r"Enter port": "dummy",
            r"Enter database/SID": "dummy",
        }
    )

    mock_workspace_client.secrets.list_scopes.side_effect = [[SecretScope(name="scope_name")]]

    with patch(
        "databricks.labs.lakebridge.helpers.recon_config_utils.ReconConfigPrompts._secret_key_exists",
        return_value=False,
    ):
        recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
        recon_conf.prompt_source()

        recon_conf.prompt_and_save_connection_details()


def test_configure_secrets_invalid_source(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Select the source": "100",  # Invalid source
            r"Enter Secret Scope name": SCOPE_NAME,
        }
    )

    with patch(
        "databricks.labs.lakebridge.helpers.recon_config_utils.ReconConfigPrompts._scope_exists",
        return_value=True,
    ):
        recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
        with pytest.raises(ValueError, match="cannot get answer within 10 attempt"):
            recon_conf.prompt_source()


def test_store_connection_secrets_exception(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Do you want to overwrite `source_key`?": "no",
        }
    )

    mock_workspace_client.secrets.get_secret.side_effect = ResourceDoesNotExist("Not Found")
    mock_workspace_client.secrets.put_secret.side_effect = Exception("Timed out")

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)

    with pytest.raises(Exception, match="Timed out"):
        recon_conf.store_connection_secrets("scope_name", ("source", {"key": "value"}))


def test_configure_secrets_no_scope(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Select the source": SOURCE_DICT["snowflake"],
            r"Enter Secret Scope name": SCOPE_NAME,
            r"Do you want to create a new one?": "no",
        }
    )

    mock_workspace_client.secrets.list_scopes.side_effect = [[SecretScope(name="scope_name")]]

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    with pytest.raises(SystemExit, match="Scope is needed to store Secrets in Databricks Workspace"):
        recon_conf.prompt_and_save_connection_details()


def test_configure_secrets_create_scope_exception(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Select the source": SOURCE_DICT["snowflake"],
            r"Enter Secret Scope name": SCOPE_NAME,
            r"Do you want to create a new one?": "yes",
        }
    )

    mock_workspace_client.secrets.list_scopes.side_effect = [[SecretScope(name="scope_name")]]
    mock_workspace_client.secrets.create_scope.side_effect = Exception("Network Error")

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    with pytest.raises(Exception, match="Network Error"):
        recon_conf.prompt_and_save_connection_details()


def test_store_connection_secrets_overwrite(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Do you want to overwrite `key`?": "no",
        }
    )

    with patch(
        "databricks.labs.lakebridge.helpers.recon_config_utils.ReconConfigPrompts._secret_key_exists", return_value=True
    ):
        recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
        recon_conf.store_connection_secrets("scope_name", ("source", {"key": "value"}))
