import pytest
from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, MSSQLConnector


def test_mssql_connector_connection(sandbox_sqlserver):
    assert isinstance(sandbox_sqlserver.connector, MSSQLConnector)


def test_mssql_connector_execute_query(sandbox_sqlserver):
    # Test executing a query
    query = "SELECT 101 AS test_column"
    result = sandbox_sqlserver.fetch(query).rows
    assert result[0][0] == 101


def test_connection_test(sandbox_sqlserver):
    assert sandbox_sqlserver.check_connection()


@pytest.mark.skip(
    reason="Requires Azure SPN credentials (TOOLS_CLIENT_ID / TOOLS_CLIENT_SECRET) — not available in CI."
)
def test_spn_authentication_connection(sandbox_spn_sqlserver_config: JsonObject) -> None:
    dbm = DatabaseManager("mssql", sandbox_spn_sqlserver_config)
    assert dbm.check_connection()
