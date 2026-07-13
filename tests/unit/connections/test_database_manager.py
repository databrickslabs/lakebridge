from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.assessments.errors import ErrorCategory, SourceFailure, SourceQueryError
from databricks.labs.lakebridge.connections.database_manager import (
    DatabaseManager,
    MSSQLConnector,
    RedshiftConnector,
    TeradataConnector,
)

sample_config: JsonObject = {
    'user': 'test_user',
    'password': 'test_pass',
    'server': 'test_server',
    'database': 'test_db',
    'driver': 'ODBC Driver 17 for SQL Server',
    'trust_server_certificate': False,
}

redshift_config: JsonObject = {
    "host": "test-cluster.example.com",
    "database": "test_db",
    "user": "test_user",
    "password": "test_pass",
}

teradata_config: JsonObject = {
    "host": "test-host",
    "user": "test_user",
    "password": "test_pass",
    "database": "test_db",
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
def test_fetch_raises_source_query_error_for_absence(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    orig = MagicMock()
    orig.sqlstate = "42P01"
    operational_error = OperationalError("statement", {}, Exception("relation does not exist"))
    operational_error.orig = orig
    mock_connector_instance.fetch.side_effect = operational_error
    mock_connector_instance.parse_source_error.return_value = SourceFailure(
        category=ErrorCategory.ABSENCE,
        reason="relation does not exist",
        sqlstate="42P01",
    )

    db_manager = DatabaseManager("mssql", sample_config)

    with pytest.raises(SourceQueryError) as exc_info:
        db_manager.fetch("SELECT 1")

    assert exc_info.value.category == ErrorCategory.ABSENCE
    assert exc_info.value.sqlstate == "42P01"


@patch('databricks.labs.lakebridge.connections.database_manager.TeradataConnector')
def test_fetch_raises_source_query_error_for_teradata_missing_database(mock_teradata_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_teradata_connector.return_value = mock_connector_instance

    orig = Exception("[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist.")
    mock_connector_instance.fetch.side_effect = OperationalError("statement", {}, orig)
    mock_connector_instance.parse_source_error.return_value = SourceFailure(
        category=ErrorCategory.ABSENCE,
        reason="[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist.",
        sqlstate="42S02",
        vendor_code="3802",
    )

    db_manager = DatabaseManager("teradata", sample_config)

    with pytest.raises(SourceQueryError) as exc_info:
        db_manager.fetch("SELECT 1 FROM PDCRINFO.DBQLogTbl_Hst")

    assert exc_info.value.category == ErrorCategory.ABSENCE
    assert exc_info.value.sqlstate == "42S02"


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_base_connector_parse_source_error_from_sqlalchemy_orig(create_engine_mock) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = MSSQLConnector(sample_config)

    orig = MagicMock()
    orig.sqlstate = "42601"
    error = OperationalError("statement", {}, orig)

    assert connector.parse_source_error(error) == SourceFailure(
        category=ErrorCategory.SYNTAX,
        reason=str(orig),
        sqlstate="42601",
    )


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_connector_parse_source_error_from_pyodbc_args(create_engine_mock) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = MSSQLConnector(sample_config)

    class _PyodbcError(Exception):
        pass

    orig = _PyodbcError("42S02", "[42S02] [Microsoft][ODBC Driver 18 for SQL Server]Invalid object name 'x'. (208)")
    error = OperationalError("statement", {}, Exception("placeholder"))
    error.orig = orig

    assert connector.parse_source_error(error) == SourceFailure(
        category=ErrorCategory.ABSENCE,
        reason="('42S02', \"[42S02] [Microsoft][ODBC Driver 18 for SQL Server]Invalid object name 'x'. (208)\")",
        sqlstate="42S02",
    )


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_mssql_connector_ignores_non_sqlstate_args(create_engine_mock) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = MSSQLConnector(sample_config)

    class _DriverError(Exception):
        pass

    orig = _DriverError("connection to server failed")
    error = OperationalError("statement", {}, Exception("placeholder"))
    error.orig = orig

    failure = connector.parse_source_error(error)

    assert failure.sqlstate is None
    assert failure.category == ErrorCategory.UNKNOWN


@patch('databricks.labs.lakebridge.connections.database_manager.redshift_connector.connect')
def test_redshift_connector_parse_source_error_from_error_dict(connect_mock) -> None:
    connect_mock.return_value = MagicMock()
    connector = RedshiftConnector(redshift_config)
    error = Exception({"C": "42P01", "M": "relation does not exist"})

    assert connector.parse_source_error(error) == SourceFailure(
        category=ErrorCategory.ABSENCE,
        reason=str(error),
        sqlstate="42P01",
    )


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_teradata_connector_parse_source_error_from_message(create_engine_mock) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = TeradataConnector(teradata_config)

    orig = Exception(
        "[Version 20.0.0.61] [Session 1] [Teradata Database] "
        "[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist."
    )
    error = OperationalError("statement", {}, orig)

    assert connector.parse_source_error(error) == SourceFailure(
        category=ErrorCategory.ABSENCE,
        reason=str(orig),
        sqlstate="42S02",
        vendor_code="3802",
    )


@pytest.mark.parametrize(
    ("message", "expected_category", "expected_vendor_code"),
    [
        ("[Error 3802] Database 'pdcrinfo' does not exist", ErrorCategory.ABSENCE, "3802"),
        ("[Error 3807] Object 'FOO' does not exist", ErrorCategory.ABSENCE, "3807"),
        ("[Error 3523] The user does not have privilege", ErrorCategory.PERMISSION, "3523"),
        ("[Error 9999] something else", ErrorCategory.UNKNOWN, "9999"),
    ],
)
@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_teradata_connector_classifies_vendor_codes_without_sqlstate(
    create_engine_mock,
    message: str,
    expected_category: ErrorCategory,
    expected_vendor_code: str,
) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = TeradataConnector(teradata_config)
    error = OperationalError("statement", {}, Exception(message))

    failure = connector.parse_source_error(error)

    assert failure.category == expected_category
    assert failure.vendor_code == expected_vendor_code
    assert failure.sqlstate is None


@patch('databricks.labs.lakebridge.connections.database_manager.create_engine')
def test_teradata_connector_sqlstate_takes_precedence_over_vendor_code(create_engine_mock) -> None:
    create_engine_mock.return_value = MagicMock()
    connector = TeradataConnector(teradata_config)
    message = "[Error 3807] [SQLState 42601] misleading"
    error = OperationalError("statement", {}, Exception(message))

    failure = connector.parse_source_error(error)

    assert failure.category == ErrorCategory.SYNTAX
    assert failure.sqlstate == "42601"
    assert failure.vendor_code == "3807"
