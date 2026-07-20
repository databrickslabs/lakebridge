from unittest.mock import MagicMock, patch

import pytest
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


@patch('databricks.labs.lakebridge.connections.database_manager.TeradataConnector')
def test_teradata_connector(mock_teradata_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_teradata_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("teradata", sample_config)

    assert db_manager.connector == mock_connector_instance
    mock_teradata_connector.assert_called_once_with(sample_config)


clickhouse_config: JsonObject = {
    'host': '127.0.0.1',
    'port': 8123,
    'user': 'default',
    'password': 'test_pass',
    'secure': False,
}


@patch('databricks.labs.lakebridge.connections.database_manager.ClickHouseConnector')
def test_clickhouse_connector_registered(mock_clickhouse_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_clickhouse_connector.return_value = mock_connector_instance

    db_manager = DatabaseManager("clickhouse", clickhouse_config)

    assert db_manager.connector == mock_connector_instance
    mock_clickhouse_connector.assert_called_once_with(clickhouse_config)


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_fetch_maps_result(mock_clickhouse_connect) -> None:
    """fetch() maps the clickhouse-connect result (column_names/result_rows) into a FetchResult."""
    mock_client = MagicMock()
    mock_clickhouse_connect.get_client.return_value = mock_client
    mock_client.query.return_value = MagicMock(
        column_names=["engine_edition", "name"],
        result_rows=[[3, "prod"]],
    )

    db_manager = DatabaseManager("clickhouse", clickhouse_config)
    result = db_manager.fetch("SELECT 1")

    assert result.columns == {"engine_edition", "name"}
    assert result.rows == [[3, "prod"]]
    mock_client.query.assert_called_once_with("SELECT 1")


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_health_check_and_close(mock_clickhouse_connect) -> None:
    mock_client = MagicMock()
    mock_clickhouse_connect.get_client.return_value = mock_client
    mock_client.query.return_value = MagicMock(column_names=["test_column"], result_rows=[[101]])

    with DatabaseManager("clickhouse", clickhouse_config) as db_manager:
        assert db_manager.check_connection() is True

    # __exit__ closes the underlying client.
    mock_client.close.assert_called_once()


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_secure_default_is_host_derived(mock_clickhouse_connect) -> None:
    """With `secure` absent, a *.clickhouse.cloud host defaults to TLS on 8443; any other host
    defaults to plaintext on 8123 (never insecure-by-default for Cloud)."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()

    DatabaseManager("clickhouse", {"host": "abc.us-east-1.aws.clickhouse.cloud", "password": "p"})
    cloud_kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert cloud_kwargs["secure"] is True
    assert cloud_kwargs["port"] == 8443

    mock_clickhouse_connect.get_client.reset_mock()
    DatabaseManager("clickhouse", {"host": "10.0.0.5", "password": "p"})
    oss_kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert oss_kwargs["secure"] is False
    assert oss_kwargs["port"] == 8123


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_cloud_host_cannot_be_downgraded(mock_clickhouse_connect) -> None:
    """A stray `secure: "false"` (or bool False) in a hand-written creds file must NOT downgrade a
    Cloud host to plaintext — the connection carries the password. bool("false") is True in Python,
    so this also guards against a naive bool() coercion silently doing the right thing by accident."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()

    for bad_secure in ("false", False, "no", 0):
        mock_clickhouse_connect.get_client.reset_mock()
        DatabaseManager(
            "clickhouse",
            {"host": "abc.us-east-1.aws.clickhouse.cloud", "password": "p", "secure": bad_secure},
        )
        kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
        assert kwargs["secure"] is True, f"cloud host downgraded with secure={bad_secure!r}"
        assert kwargs["port"] == 8443


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_parses_string_secure_true(mock_clickhouse_connect) -> None:
    """A non-Cloud host with secure="true" (string, from a creds file) must connect with TLS."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()
    DatabaseManager("clickhouse", {"host": "10.0.0.5", "password": "p", "secure": "true"})
    kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert kwargs["secure"] is True
    assert kwargs["port"] == 8443
