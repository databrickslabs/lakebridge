import os
import pytest
from unittest.mock import MagicMock, patch
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

sample_config = {
    'user': 'test_user',
    'password': 'test_pass',
    'server': 'test_server',
    'database': 'test_db',
    'driver': 'ODBC Driver 17 for SQL Server',
}


def test_create_connector_unsupported_db_type():
    with pytest.raises(ValueError, match="Unsupported database type: unsupported_db"):
        DatabaseManager("unsupported_db", sample_config)


# Test case for MSSQLConnector
@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector):
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_execute_query(mock_mssql_connector):
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    query = "SELECT * FROM users"
    mock_result = MagicMock()
    mock_connector_instance.execute_query.return_value = mock_result

    result = db_manager.execute_query(query)

    assert result == mock_result
    mock_connector_instance.execute_query.assert_called_once_with(query)


def running_on_ci() -> bool:
    """Return True if the tests are running within a CI environment."""
    env_vars = {"CI", "BUILD_NUMBER"}
    return any(var in os.environ for var in env_vars)


def odbc_available() -> bool:
    """Return True of the ODBC driver is available."""
    try:
        import pyodbc  # type: ignore[import]

        assert pyodbc.version is not None

        return True
    except ImportError:
        return False


# Only allow this to be skipped during local development: the CI environment must be set up to run this test.
@pytest.mark.xfail(
    not running_on_ci() and not odbc_available(),
    reason="This test needs native ODBC libraries to be installed.",
    raises=ImportError,
)
def test_execute_query_without_connection():
    db_manager = DatabaseManager("mssql", sample_config)

    # Simulating that the engine is not connected
    db_manager.connector.engine = None

    with pytest.raises(ConnectionError, match="Not connected to the database."):
        db_manager.execute_query("SELECT * FROM users")
