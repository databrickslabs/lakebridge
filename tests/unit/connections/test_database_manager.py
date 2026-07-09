from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.assessments.errors import ErrorCategory, SourceQueryError
from databricks.labs.lakebridge.connections.database_manager import (
    DatabaseManager,
    TeradataConnector,
    _BaseConnector,
    extract_sqlstate,
)

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


@patch('databricks.labs.lakebridge.connections.database_manager.MSSQLConnector')
def test_fetch_raises_source_query_error_for_absence(mock_mssql_connector) -> None:
    mock_connector_instance = MagicMock()
    mock_mssql_connector.return_value = mock_connector_instance

    orig = MagicMock()
    orig.sqlstate = "42P01"
    operational_error = OperationalError("statement", {}, Exception("relation does not exist"))
    operational_error.orig = orig
    mock_connector_instance.fetch.side_effect = operational_error

    db_manager = DatabaseManager("mssql", sample_config)

    with pytest.raises(SourceQueryError) as exc_info:
        db_manager.fetch("SELECT 1")

    assert exc_info.value.category == ErrorCategory.ABSENCE
    assert exc_info.value.sqlstate == "42P01"


def test_extract_sqlstate_from_redshift_error_dict() -> None:
    error = Exception({"C": "42P01", "M": "relation does not exist"})
    assert extract_sqlstate(error) == "42P01"


def test_extract_sqlstate_from_sqlalchemy_orig() -> None:
    orig = MagicMock()
    orig.sqlstate = "42601"
    error = OperationalError("statement", {}, orig)
    assert extract_sqlstate(error) == "42601"


def test_extract_sqlstate_from_pyodbc_args() -> None:
    """pyodbc has no .sqlstate attribute; SQLSTATE is the first element of args."""

    class _PyodbcError(Exception):
        pass

    orig = _PyodbcError("42S02", "[42S02] [Microsoft][ODBC Driver 18 for SQL Server]Invalid object name 'x'. (208)")
    error = OperationalError("statement", {}, Exception("placeholder"))
    error.orig = orig
    assert extract_sqlstate(error) == "42S02"


def test_extract_sqlstate_ignores_non_sqlstate_args() -> None:
    """A message-only first arg (not a 5-char SQLSTATE) must not be mistaken for a code."""

    class _DriverError(Exception):
        pass

    orig = _DriverError("connection to server failed")
    error = OperationalError("statement", {}, Exception("placeholder"))
    error.orig = orig
    assert extract_sqlstate(error) is None


# teradatasql exposes no SQLSTATE attribute, so the generic extract_sqlstate() cannot read
# it; TeradataConnector parses the SQLSTATE embedded in the driver message instead. This
# parsing is deliberately kept out of the shared helper so other connectors are unaffected.
def test_shared_extract_sqlstate_cannot_read_teradata_message() -> None:
    orig = Exception("[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist.")
    error = OperationalError("statement", {}, orig)
    assert extract_sqlstate(error) is None


def test_teradata_extract_sqlstate_from_message() -> None:
    orig = Exception(
        "[Version 20.0.0.61] [Session 1] [Teradata Database] "
        "[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist."
    )
    error = OperationalError("statement", {}, orig)
    assert TeradataConnector._extract_sqlstate(error) == "42S02"


def test_teradata_extract_sqlstate_none_without_token() -> None:
    error = OperationalError("statement", {}, Exception("some non-teradata failure"))
    assert TeradataConnector._extract_sqlstate(error) is None


@patch.object(TeradataConnector, "_connect", return_value=MagicMock())
def test_teradata_fetch_classifies_missing_database_as_absence(_mock_connect) -> None:
    """A missing PDCRINFO database (Teradata error 3802 / SQLSTATE 42S02) must degrade to
    ABSENCE so optional PDCR steps can tolerate it, rather than aborting the pipeline."""
    orig = Exception("[Error 3802] [SQLState 42S02] Database 'pdcrinfo' does not exist.")
    operational_error = OperationalError("statement", {}, orig)

    connector = TeradataConnector(sample_config)
    with patch.object(_BaseConnector, "fetch", side_effect=operational_error):
        with pytest.raises(SourceQueryError) as exc_info:
            connector.fetch("SELECT 1 FROM PDCRINFO.DBQLogTbl_Hst")

    assert exc_info.value.category == ErrorCategory.ABSENCE
    assert exc_info.value.sqlstate == "42S02"


@patch.object(TeradataConnector, "_connect", return_value=MagicMock())
def test_teradata_fetch_propagates_source_query_error(_mock_connect) -> None:
    """An already-classified SourceQueryError must pass through unchanged (not re-wrapped)."""
    sentinel = SourceQueryError(ErrorCategory.CONNECTION, "08001", "connection refused")

    connector = TeradataConnector(sample_config)
    with patch.object(_BaseConnector, "fetch", side_effect=sentinel):
        with pytest.raises(SourceQueryError) as exc_info:
            connector.fetch("SELECT 1")

    assert exc_info.value is sentinel
