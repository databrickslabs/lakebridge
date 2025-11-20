import pytest

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.lakebridge.helpers.recon_config_utils import ReconConfigPrompts
from databricks.sdk.errors.platform import ResourceDoesNotExist

from databricks.labs.lakebridge.reconcile.constants import ReconSourceType


def test_configure_secrets_snowflake(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Enter secret vault type": "0",
            r"Enter Snowflake URL": "dummy",
            r"Enter User": "dummy",
            r"Enter Password*": "dummy",
            r"Enter Database": "dummy",
            r"Enter Schema": "dummy",
            r"Enter Snowflake Warehouse": "dummy",
            r"Enter Role": "dummy",
        }
    )
    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_recon_creds(ReconSourceType.SNOWFLAKE.value)


def test_configure_secrets_snowflake_pem(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Enter secret vault type": "0",
            r"Enter Snowflake URL": "dummy",
            r"Enter User": "dummy",
            r"Enter Password*": "",
            r"Enter PEM*": "dummy",
            r"Enter PEM*Password*": "dummy",
            r"Enter Database": "dummy",
            r"Enter Schema": "dummy",
            r"Enter Snowflake Warehouse": "dummy",
            r"Enter Role": "dummy",
        }
    )
    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_recon_creds(ReconSourceType.SNOWFLAKE.value)


def test_configure_secrets_oracle(mock_workspace_client):
    # mock prompts for Oracle
    prompts = MockPrompts(
        {
            r"Enter secret vault type": "1",
            r"Do you want to create a new one?": "yes",
            r"Enter User": "dummy",
            r"Enter Password": "dummy",
            r"Enter host": "dummy",
            r"Enter port": "dummy",
            r"Enter database/SID": "dummy",
        }
    )

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_recon_creds(ReconSourceType.ORACLE.value)


def test_configure_secrets_tsql(mock_workspace_client):
    prompts = MockPrompts(
        {
            r"Enter secret vault type": "2",
            r"Enter User": "dummy",
            r"Enter Password": "dummy",
            r"Enter host": "dummy",
            r"Enter port": "dummy",
            r"Enter database": "dummy",
            r"Enter Encrypt": "dummy",
            r"Enter Trust Server Certificate": "dummy",
        }
    )

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_recon_creds(ReconSourceType.MSSQL.value)
    recon_conf.prompt_recon_creds(ReconSourceType.SYNAPSE.value)


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
