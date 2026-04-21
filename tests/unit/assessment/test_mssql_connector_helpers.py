import logging
from unittest.mock import MagicMock

from databricks.labs.lakebridge.resources.assessments.mssql.common.connector import get_query_class
from databricks.labs.lakebridge.resources.assessments.mssql.common.queries import (
    AzureSQLQueries,
    MSSQLQueries,
)


def _mock_connection_returning(rows) -> MagicMock:
    conn = MagicMock()
    conn.fetch.return_value = MagicMock(rows=rows)
    return conn


def test_get_query_class_returns_azure_for_engine_edition_5() -> None:
    conn = _mock_connection_returning([[5]])
    assert get_query_class(conn) is AzureSQLQueries


def test_get_query_class_returns_mssql_for_on_prem_edition() -> None:
    conn = _mock_connection_returning([[3]])
    assert get_query_class(conn) is MSSQLQueries


def test_get_query_class_returns_mssql_when_no_rows() -> None:
    conn = _mock_connection_returning([])
    assert get_query_class(conn) is MSSQLQueries


def test_get_query_class_falls_back_when_detection_fails(caplog) -> None:
    conn = MagicMock()
    conn.fetch.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        result = get_query_class(conn)

    assert result is MSSQLQueries
    assert any("Could not detect engine edition" in r.message for r in caplog.records)
