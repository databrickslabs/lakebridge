from unittest.mock import MagicMock, patch

import mssql_python
import pytest

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


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_mssql_connector(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    connector = create_connector("mssql", sample_config)

    assert connector == mock_connector_instance
    mock_mssql_connector.assert_called_once_with(sample_config)


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

    connector = create_connector("clickhouse", clickhouse_config)

    assert connector == mock_connector_instance
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

    connector = create_connector("clickhouse", clickhouse_config)
    result = connector.fetch("SELECT 1")

    assert result.columns == ["engine_edition", "name"]
    assert result.rows == [[3, "prod"]]
    mock_client.query.assert_called_once_with("SELECT 1")


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_health_check_and_close(mock_clickhouse_connect) -> None:
    mock_client = MagicMock()
    mock_clickhouse_connect.get_client.return_value = mock_client
    mock_client.query.return_value = MagicMock(column_names=["test_column"], result_rows=[[101]])

    with create_connector("clickhouse", clickhouse_config) as connector:
        assert connector.health_check() is True

    # __exit__ closes the underlying client.
    mock_client.close.assert_called_once()


@patch('databricks.labs.lakebridge.connections.database_manager.clickhouse_connect')
def test_clickhouse_connector_secure_default_is_host_derived(mock_clickhouse_connect) -> None:
    """With `secure` absent, a *.clickhouse.cloud host defaults to TLS on 8443; any other host
    defaults to plaintext on 8123 (never insecure-by-default for Cloud)."""
    mock_clickhouse_connect.get_client.return_value = MagicMock()

    create_connector("clickhouse", {"host": "abc.us-east-1.aws.clickhouse.cloud", "password": "p"})
    cloud_kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert cloud_kwargs["secure"] is True
    assert cloud_kwargs["port"] == 8443

    mock_clickhouse_connect.get_client.reset_mock()
    create_connector("clickhouse", {"host": "10.0.0.5", "password": "p"})
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
        create_connector(
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
    create_connector("clickhouse", {"host": "10.0.0.5", "password": "p", "secure": "true"})
    kwargs = mock_clickhouse_connect.get_client.call_args.kwargs
    assert kwargs["secure"] is True
    assert kwargs["port"] == 8443


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
