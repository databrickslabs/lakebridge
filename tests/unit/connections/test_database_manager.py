import pytest
from unittest.mock import MagicMock, patch
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, MSSQLConnector

sample_config = {
    'user': 'test_user',
    'password': 'test_pass',
    'server': 'test_server',
    'database': 'test_db',
    'driver': 'ODBC Driver 17 for SQL Server',
}


def test_create_connector_unsupported_db_type() -> None:
    with pytest.raises(ValueError, match="Unsupported database type: unsupported_db"):
        DatabaseManager("unsupported_db", sample_config)


# Test case for MSSQLConnector
@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    query = "SELECT * FROM users"
    mock_result = MagicMock()
    mock_connector_instance.fetch.return_value = mock_result

    result = db_manager.fetch(query)

    assert result == mock_result
    mock_connector_instance.fetch.assert_called_once_with(query)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch_commit(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    mutate_query = "TRUNCATE users"
    mock_result = MagicMock()
    mock_connector_instance.fetch.return_value = mock_result

    mutate_result = db_manager.fetch(mutate_query)

    assert mutate_result == mock_result
    mock_connector_instance.fetch.assert_called_once_with(mutate_query)


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_connector_strips_whitespace_from_credentials(mock_create_engine) -> None:
    """Test that MSSQLConnector strips leading/trailing whitespace from all credential values."""
    config_with_whitespace = {
        'user': '  test_user  ',
        'password': 'test_pass\t',
        'server': '\ntest_server ',
        'database': ' test_db',
        'driver': 'ODBC Driver 17 for SQL Server ',
        'auth_type': ' sql_authentication ',
    }

    mock_engine = MagicMock()
    mock_create_engine.return_value = mock_engine

    MSSQLConnector(config_with_whitespace)

    # Verify create_engine was called
    assert mock_create_engine.called

    # Get the connection string that was passed to create_engine
    call_args = mock_create_engine.call_args
    connection_string = call_args[0][0]

    # Verify stripped values in connection string
    assert connection_string.username == 'test_user'
    assert connection_string.password == 'test_pass'
    assert connection_string.host == 'test_server'
    assert connection_string.database == 'test_db'
    assert connection_string.query['driver'] == 'ODBC Driver 17 for SQL Server'


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_connector_strips_auth_type(mock_create_engine) -> None:
    """Test that MSSQLConnector strips whitespace from auth_type."""
    config_with_ad_auth = {
        'user': 'test_user',
        'password': 'test_pass',
        'server': 'test_server',
        'database': 'test_db',
        'driver': 'ODBC Driver 17 for SQL Server',
        'auth_type': ' ad_passwd_authentication ',
    }

    mock_engine = MagicMock()
    mock_create_engine.return_value = mock_engine

    MSSQLConnector(config_with_ad_auth)

    # Verify create_engine was called
    assert mock_create_engine.called

    # Get the connection string
    call_args = mock_create_engine.call_args
    connection_string = call_args[0][0]

    # Verify ActiveDirectoryPassword authentication was set
    assert connection_string.query['authentication'] == 'ActiveDirectoryPassword'


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_connector_preserves_internal_spaces(mock_create_engine) -> None:
    """Test that MSSQLConnector only strips leading/trailing whitespace, not internal spaces."""
    config_with_spaces = {
        'user': ' user with spaces ',
        'password': 'pass word',
        'server': ' server name ',
        'database': 'test db',
        'driver': ' ODBC Driver 17 for SQL Server ',
    }

    mock_engine = MagicMock()
    mock_create_engine.return_value = mock_engine

    MSSQLConnector(config_with_spaces)

    # Verify create_engine was called
    assert mock_create_engine.called

    # Get the connection string
    call_args = mock_create_engine.call_args
    connection_string = call_args[0][0]

    # Verify internal spaces are preserved
    assert connection_string.username == 'user with spaces'
    assert connection_string.password == 'pass word'
    assert connection_string.host == 'server name'
    assert connection_string.database == 'test db'
    assert connection_string.query['driver'] == 'ODBC Driver 17 for SQL Server'
