import pytest
from unittest.mock import MagicMock, patch
from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

sample_config: JsonObject = {
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


def _mssql_config_without(key: str) -> dict:
    cfg = dict(sample_config)
    cfg.pop(key, None)
    return cfg


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_default_database_is_master(mock_create_engine) -> None:
    DatabaseManager("mssql", _mssql_config_without('database'))
    url = mock_create_engine.call_args.args[0]
    assert url.database == 'master'


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_empty_database_falls_back_to_master(mock_create_engine) -> None:
    cfg = dict(sample_config, database='')
    DatabaseManager("mssql", cfg)
    url = mock_create_engine.call_args.args[0]
    assert url.database == 'master'


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_trust_server_certificate_passed_through(mock_create_engine) -> None:
    cfg = dict(sample_config, trust_server_certificate='yes')
    DatabaseManager("mssql", cfg)
    url = mock_create_engine.call_args.args[0]
    assert url.query.get('TrustServerCertificate') == 'yes'


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_trust_server_certificate_defaults_to_no(mock_create_engine) -> None:
    DatabaseManager("mssql", sample_config)
    url = mock_create_engine.call_args.args[0]
    assert url.query.get('TrustServerCertificate') == 'no'
