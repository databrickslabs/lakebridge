import mssql_python
import pytest
from unittest.mock import MagicMock, patch
from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import create_connector, MSSQLConnector

sample_config: JsonObject = {
    'user': 'test_user',
    'password': 'test_pass',
    'server': 'test_server',
    'database': 'test_db',
    'trust_server_certificate': False,
}


def test_create_connector_unsupported_db_type() -> None:
    with pytest.raises(ValueError, match="Unsupported database type: unsupported_db"):
        create_connector("unsupported_db", sample_config)


# Test case for MSSQLConnector
@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    connector = create_connector("mssql", sample_config)

    assert connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


# Test case for legacy_synapse (Azure Synapse dedicated SQL pool — dispatches to MSSQLConnector)
@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_legacy_synapse_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    connector = create_connector("legacy_synapse", sample_config)

    assert connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    connector = create_connector("mssql", sample_config)

    query = "SELECT * FROM users"
    mock_result = MagicMock()
    mock_connector_instance.fetch.return_value = mock_result

    result = connector.fetch(query)

    assert result == mock_result
    mock_connector_instance.fetch.assert_called_once_with(query)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch_commit(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    connector = create_connector("mssql", sample_config)

    mutate_query = "TRUNCATE users"
    mock_result = MagicMock()
    mock_connector_instance.fetch.return_value = mock_result

    mutate_result = connector.fetch(mutate_query)

    assert mutate_result == mock_result
    mock_connector_instance.fetch.assert_called_once_with(mutate_query)


@patch('databricks.labs.lakebridge.connections.database_manager.TeradataConnector')
def test_teradata_connector(mock_teradata_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_teradata_connector.return_value = mock_connector_instance

    connector = create_connector("teradata", sample_config)

    assert connector == mock_connector_instance
    mock_teradata_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.mssql_python.connect')
def test_mssql_connect_failure_raises_connection_error(mock_connect) -> None:
    mock_connect.side_effect = mssql_python.OperationalError("login failed", "ddbc details")

    with pytest.raises(ConnectionError, match="test_server"):
        MSSQLConnector(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.mssql_python.connect')
def test_mssql_fetch_error_is_cleaned_to_connection_error(mock_connect) -> None:
    """A raw driver query error surfaces as a ConnectionError whose message keeps the
    concise first line of the driver error (not an empty string)."""
    cursor = MagicMock()
    cursor.execute.side_effect = mssql_python.ProgrammingError(
        "Invalid column name 'foo'", "DDBC detail\nstack line 2\nstack line 3"
    )
    mock_connect.return_value.cursor.return_value = cursor

    connector = MSSQLConnector(sample_config)
    with pytest.raises(ConnectionError) as exc:
        connector.fetch("SELECT foo FROM bar")

    message = str(exc.value)
    assert message.startswith("Database query failed: ")
    # non-empty reason, and the multi-line stack is stripped to the first line
    assert message != "Database query failed: "
    assert "\n" not in message
    assert "stack line 2" not in message
