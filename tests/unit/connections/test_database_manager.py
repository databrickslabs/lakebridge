import pytest
from unittest.mock import MagicMock, patch
from typing import Any, cast
from sqlalchemy.exc import OperationalError
from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, FetchResult

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


# Test case for MSSQLConnector
@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


# Test case for legacy_synapse (Azure Synapse dedicated SQL pool — dispatches to MSSQLConnector)
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
def test_probe_returns_true_on_success(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    query = "SELECT 1"
    assert db_manager.probe(query) is True
    mock_connector_instance.fetch.assert_called_once_with(query)


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_probe_returns_false_on_operational_error(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_connector_instance.fetch.side_effect = OperationalError("stmt", {}, Exception("relation missing"))
    mock_mssql_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("mssql", sample_config)

    # A failed probe is a handled outcome: it must not raise.
    assert db_manager.probe("SELECT 1 FROM missing_db.missing_tbl") is False


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


def test_fetch_result_to_df_normalizes_utf8_text() -> None:
    # Includes APJ + European-language text and malformed surrogate text to validate normalization path.
    raw_rows = [
        (
            "こんにちは",  # Japanese
            "안녕하세요",  # Korean
            "สวัสดี".encode("utf-8"),  # Thai bytes
            "Bonjour",  # French
            "Müller",  # German
            "Ciao",  # Italian
            "José García",  # Spanish
            "João Silva",  # Portuguese
            "Pieter de Vries",  # Dutch
            "\udcff",  # malformed surrogate
        )
    ]
    result = FetchResult(
        columns={"ja", "ko", "th", "fr", "de", "it", "es", "pt", "nl", "bad_text"},
        rows=cast(Any, raw_rows),
    )

    frame = result.to_df()

    assert frame.iloc[0, 0] == "こんにちは"
    assert frame.iloc[0, 1] == "안녕하세요"
    assert frame.iloc[0, 2] == "สวัสดี"
    assert frame.iloc[0, 3] == "Bonjour"
    assert frame.iloc[0, 4] == "Müller"
    assert frame.iloc[0, 5] == "Ciao"
    assert frame.iloc[0, 6] == "José García"
    assert frame.iloc[0, 7] == "João Silva"
    assert frame.iloc[0, 8] == "Pieter de Vries"
    assert frame.iloc[0, 9] == "?"
