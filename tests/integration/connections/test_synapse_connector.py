import pytest
from databricks.labs.blueprint.installation import JsonObject

from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, MSSQLConnector, create_connector
from databricks.labs.lakebridge.connections.synapse_connection_helpers import create_synapse_connection
from tests.integration.debug_envgetter import TestEnvGetter


def _get_synapse_workspace(cred_config: JsonObject) -> dict:
    synapse = cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    return workspace_config


def test_synapse_connector_connection(sandbox_synapse: DatabaseConnector) -> None:
    """Test that the Synapse factory returns an MSSQLConnector."""
    assert isinstance(sandbox_synapse, MSSQLConnector)


def test_synapse_connector_execute_query(sandbox_synapse: DatabaseConnector) -> None:
    """Test executing a query through the Synapse connector."""
    query = "SELECT 101 AS test_column"
    result = sandbox_synapse.fetch(query).rows
    assert result[0][0] == 101


def test_synapse_connection_check(sandbox_synapse: DatabaseConnector) -> None:
    """Test connection check for Synapse."""
    assert sandbox_synapse.health_check()


def test_synapse_with_credential_format(sandbox_synapse_cred_config: JsonObject) -> None:
    """Test the connector with credential format."""
    synapse = sandbox_synapse_cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    profiler = synapse["profiler"]
    assert isinstance(profiler, dict)
    db_name = profiler["databases"]

    # Simulate what the assessment code does: transform credential format to connection config
    connector = create_connector(
        "synapse",
        {
            "server": workspace_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": workspace_config['user'],
            "password": workspace_config['password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'SqlPassword',
        },
    )

    assert isinstance(connector, MSSQLConnector)
    assert connector.health_check()


def test_create_synapse_connection_sql_auth(sandbox_synapse_cred_config: JsonObject) -> None:
    """create_synapse_connection forwards workspace user/password to the connector unchanged."""
    workspace_config = _get_synapse_workspace(sandbox_synapse_cred_config)

    connector = create_synapse_connection(workspace_config, "master", auth_type="SqlPassword")

    assert isinstance(connector, MSSQLConnector)
    assert connector.config["user"] == workspace_config["user"]
    assert connector.health_check()


def test_create_synapse_connection_spn(
    sandbox_synapse_cred_config: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPN auth: connector resolves credentials from env vars, ignoring any user/password in config."""
    env = TestEnvGetter(True)
    monkeypatch.setenv("AZURE_CLIENT_ID", env.get("TOOLS_CLIENT_ID"))
    monkeypatch.setenv("AZURE_CLIENT_SECRET", env.get("TOOLS_CLIENT_SECRET"))
    # Real SPN-configured workspaces omit user/password from the YAML (the configurator skips those prompts).
    workspace_config = {
        k: v for k, v in _get_synapse_workspace(sandbox_synapse_cred_config).items() if k not in {"user", "password"}
    }

    connector = create_synapse_connection(workspace_config, "master", auth_type="ActiveDirectoryServicePrincipal")

    assert isinstance(connector, MSSQLConnector)
    assert connector.config["auth_type"] == "ActiveDirectoryServicePrincipal"
    assert connector.health_check()


def test_create_synapse_connection_endpoint_key(sandbox_synapse_cred_config: JsonObject) -> None:
    """create_synapse_connection routes to the server specified by endpoint_key."""
    workspace_config = dict(_get_synapse_workspace(sandbox_synapse_cred_config))
    # Sandbox has one server; point serverless to the same endpoint to exercise the routing
    workspace_config["serverless_sql_endpoint"] = workspace_config["dedicated_sql_endpoint"]

    connector = create_synapse_connection(workspace_config, "master", endpoint_key="serverless_sql_endpoint")

    assert isinstance(connector, MSSQLConnector)
    assert connector.config["server"] == workspace_config["serverless_sql_endpoint"]
    assert connector.health_check()


def test_synapse_query_execution(sandbox_synapse_cred_config: JsonObject) -> None:
    """Test the connector can execute queries with credential format."""
    synapse = sandbox_synapse_cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    profiler = synapse["profiler"]
    assert isinstance(profiler, dict)
    db_name = profiler["databases"]

    connector = create_connector(
        "synapse",
        {
            "server": workspace_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": workspace_config['user'],
            "password": workspace_config['password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'SqlPassword',
        },
    )

    query = "SELECT 202 AS test_column"
    result = connector.fetch(query).rows
    assert result[0][0] == 202
