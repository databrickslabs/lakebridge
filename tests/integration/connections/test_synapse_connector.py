from typing import Any

import pytest

from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, MSSQLConnector


@pytest.fixture()
def sandbox_synapse_config(sandbox_sqlserver_config: dict[str, Any]) -> dict[str, Any]:
    """Convert SQL Server config to Synapse config format for direct DatabaseManager usage."""
    # Transform MSSQL config to Synapse format
    # In testing, we use SQL Server as a stand-in for Synapse since they use the same protocol
    return {
        "server": sandbox_sqlserver_config["server"],
        "user": sandbox_sqlserver_config["user"],
        "password": sandbox_sqlserver_config["password"],
        "driver": sandbox_sqlserver_config["driver"],
        "database": sandbox_sqlserver_config["database"],
        "auth_type": "sql_authentication",
        "port": 1433,
    }


@pytest.fixture()
def sandbox_synapse_cred_config(sandbox_sqlserver_config: dict[str, Any]) -> dict[str, Any]:
    """Convert SQL Server config to Synapse credential format (as stored by configure-database-profiler)."""
    # This mimics the structure returned by credential manager for get_sqlpool_reader
    return {
        "dedicated_sql_endpoint": sandbox_sqlserver_config["server"],
        "sql_user": sandbox_sqlserver_config["user"],
        "sql_password": sandbox_sqlserver_config["password"],
        "driver": sandbox_sqlserver_config["driver"],
        "database": sandbox_sqlserver_config["database"],
    }


@pytest.fixture()
def sandbox_synapse(sandbox_synapse_config: dict[str, Any]) -> DatabaseManager:
    """Create a DatabaseManager for Synapse (uses MSSQLConnector via factory method)."""
    return DatabaseManager("synapse", sandbox_synapse_config)


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


def test_synapse_with_credential_format(sandbox_synapse_cred_config: dict[str, Any]) -> None:
    """Test DatabaseManager with credential format (sql_user/sql_password)."""
    db_name = sandbox_synapse_cred_config["database"]

    # Simulate what the assessment code does: transform credential format to connection config
    manager = DatabaseManager(
        "synapse",
        {
            "driver": sandbox_synapse_cred_config['driver'],
            "server": sandbox_synapse_cred_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": sandbox_synapse_cred_config['sql_user'],
            "password": sandbox_synapse_cred_config['sql_password'],
            "port": sandbox_synapse_cred_config.get('port', 1433),
            "auth_type": 'sql_authentication',
        },
    )

    assert isinstance(manager, DatabaseManager)
    assert isinstance(manager.connector, MSSQLConnector)
    assert manager.check_connection()


def test_synapse_query_execution(sandbox_synapse_cred_config: dict[str, Any]) -> None:
    """Test DatabaseManager can execute queries with credential format."""
    db_name = sandbox_synapse_cred_config["database"]

    manager = DatabaseManager(
        "synapse",
        {
            "driver": sandbox_synapse_cred_config['driver'],
            "server": sandbox_synapse_cred_config['dedicated_sql_endpoint'],
            "database": db_name,
            "user": sandbox_synapse_cred_config['sql_user'],
            "password": sandbox_synapse_cred_config['sql_password'],
            "port": sandbox_synapse_cred_config.get('port', 1433),
            "auth_type": 'sql_authentication',
        },
    )

    query = "SELECT 202 AS test_column"
    result = manager.fetch(query).rows
    assert result[0][0] == 202
