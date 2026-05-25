import pytest
from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, MSSQLConnector
from databricks.labs.lakebridge.connections.synapse_connection_helpers import create_synapse_connection
from tests.integration.debug_envgetter import TestEnvGetter


def _get_synapse_workspace(cred_config: JsonObject) -> dict:
    synapse = cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    return workspace_config


def test_synapse_connector_connection(sandbox_synapse: DatabaseManager) -> None:
    """Test that Synapse DatabaseManager uses MSSQLConnector."""
    assert isinstance(sandbox_synapse.connector, MSSQLConnector)


def test_synapse_connector_execute_query(sandbox_synapse: DatabaseManager) -> None:
    """Test executing a query through Synapse DatabaseManager."""
    query = "SELECT 101 AS test_column"
    result = sandbox_synapse.fetch(query).rows
    assert result[0][0] == 101


def test_synapse_connection_check(sandbox_synapse: DatabaseManager) -> None:
    """Test connection check for Synapse."""
    assert sandbox_synapse.check_connection()


def test_synapse_with_credential_format(sandbox_synapse_cred_config: JsonObject) -> None:
    """Test DatabaseManager with credential format."""
    synapse = sandbox_synapse_cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    profiler = synapse["profiler"]
    assert isinstance(profiler, dict)
    db_name = profiler["databases"]

    # Simulate what the assessment code does: transform credential format to connection config
    manager = DatabaseManager(
        "synapse",
        {
            "driver": workspace_config['driver'],
            "server": workspace_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": workspace_config['user'],
            "password": workspace_config['password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'SqlPassword',
        },
    )

    assert isinstance(manager, DatabaseManager)
    assert isinstance(manager.connector, MSSQLConnector)
    assert manager.check_connection()


def test_create_synapse_connection_sql_auth(sandbox_synapse_cred_config: JsonObject) -> None:
    """create_synapse_connection forwards workspace user/password to the connector unchanged."""
    workspace_config = _get_synapse_workspace(sandbox_synapse_cred_config)

    database_manager = create_synapse_connection(workspace_config, "master", auth_type="SqlPassword")

    assert isinstance(database_manager.connector, MSSQLConnector)
    assert database_manager.connector.config["user"] == workspace_config["user"]
    assert database_manager.check_connection()


def test_create_synapse_connection_spn(
    sandbox_synapse_cred_config: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPN auth: connector resolves credentials from env vars, ignoring any user/password in config."""
    env = TestEnvGetter(True)
    monkeypatch.setenv("AZURE_CLIENT_ID", env.get("TOOLS_CLIENT_ID"))
    monkeypatch.setenv("AZURE_CLIENT_SECRET", env.get("TOOLS_CLIENT_SECRET"))
    # Real SPN-configured workspaces omit user/password from the YAML (the configurator skips those prompts).
    workspace_config = {k: v for k, v in _get_synapse_workspace(sandbox_synapse_cred_config).items() if k not in {"user", "password"}}

    database_manager = create_synapse_connection(
        workspace_config, "master", auth_type="ActiveDirectoryServicePrincipal"
    )

    assert isinstance(database_manager.connector, MSSQLConnector)
    assert database_manager.connector.config["auth_type"] == "ActiveDirectoryServicePrincipal"
    assert database_manager.check_connection()


def test_create_synapse_connection_endpoint_key(sandbox_synapse_cred_config: JsonObject) -> None:
    """create_synapse_connection routes to the server specified by endpoint_key."""
    workspace_config = dict(_get_synapse_workspace(sandbox_synapse_cred_config))
    # Sandbox has one server; point serverless to the same endpoint to exercise the routing
    workspace_config["serverless_sql_endpoint"] = workspace_config["dedicated_sql_endpoint"]

    database_manager = create_synapse_connection(workspace_config, "master", endpoint_key="serverless_sql_endpoint")

    assert isinstance(database_manager.connector, MSSQLConnector)
    assert database_manager.connector.config["server"] == workspace_config["serverless_sql_endpoint"]
    assert database_manager.check_connection()


def test_synapse_query_execution(sandbox_synapse_cred_config: JsonObject) -> None:
    """Test DatabaseManager can execute queries with credential format."""
    synapse = sandbox_synapse_cred_config["synapse"]
    assert isinstance(synapse, dict)
    workspace_config = synapse["workspace"]
    assert isinstance(workspace_config, dict)
    profiler = synapse["profiler"]
    assert isinstance(profiler, dict)
    db_name = profiler["databases"]

    manager = DatabaseManager(
        "synapse",
        {
            "driver": workspace_config['driver'],
            "server": workspace_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": workspace_config['user'],
            "password": workspace_config['password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'SqlPassword',
        },
    )

    query = "SELECT 202 AS test_column"
    result = manager.fetch(query).rows
    assert result[0][0] == 202
