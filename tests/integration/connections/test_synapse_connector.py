from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, MSSQLConnector


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
    """Test DatabaseManager with credential format (sql_user/sql_password)."""
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
            "user": workspace_config['sql_user'],
            "password": workspace_config['sql_password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'sql_authentication',
        },
    )

    assert isinstance(manager, DatabaseManager)
    assert isinstance(manager.connector, MSSQLConnector)
    assert manager.check_connection()


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
            "user": workspace_config['sql_user'],
            "password": workspace_config['sql_password'],
            "port": workspace_config.get('port', 1433),
            "auth_type": 'sql_authentication',
        },
    )

    query = "SELECT 202 AS test_column"
    result = manager.fetch(query).rows
    assert result[0][0] == 202
