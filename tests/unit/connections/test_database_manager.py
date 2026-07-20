from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

sample_config: JsonObject = {
    'user': 'test_user',
    'password': 'test_pass',
    'server': 'test_server',
    'database': 'test_db',
    'driver': 'ODBC Driver 17 for SQL Server',
    'trust_server_certificate': False,
}


def test_create_connector_unsupported_db_type() -> None:
    with pytest.raises(ValueError, match="Unsupported database type: unsupported_db"):
        DatabaseManager("unsupported_db", sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_legacy_synapse_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("legacy_synapse", sample_config)

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


@patch('databricks.labs.lakebridge.connections.database_manager.TeradataConnector')
def test_teradata_connector(mock_teradata_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_teradata_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("teradata", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_teradata_connector.assert_called_once_with(sample_config)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch_raises_connection_error_with_concise_message(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    operational_error = OperationalError(
        "statement",
        {},
        Exception("relation does not exist\nfull driver stack trace and SQL dump"),
    )
    mock_connector_instance.fetch.side_effect = operational_error

    db_manager = DatabaseManager("mssql", sample_config)

    with pytest.raises(ConnectionError, match="Database query failed: relation does not exist") as exc_info:
        db_manager.fetch("SELECT 1")

    assert "full driver stack trace" not in str(exc_info.value)


@patch('databricks.labs.lakebridge.connections.database_manager.TeradataConnector')
def test_fetch_raises_connection_error_for_teradata_missing_database(mock_teradata_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_teradata_connector.return_value = mock_connector_instance

    orig = Exception("[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist.")
    mock_connector_instance.fetch.side_effect = OperationalError("statement", {}, orig)

    db_manager = DatabaseManager("teradata", sample_config)

    with pytest.raises(ConnectionError, match="Database 'pdcrinfo' does not exist"):
        db_manager.fetch("SELECT 1 FROM PDCRINFO.DBQLogTbl_Hst")


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_check_connection_raises_connection_error(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance
    mock_connector_instance.health_check.side_effect = OperationalError("statement", {}, Exception("login failed"))

    db_manager = DatabaseManager("mssql", sample_config)

    with pytest.raises(ConnectionError, match="Database health check failed: login failed"):
        db_manager.check_connection()
