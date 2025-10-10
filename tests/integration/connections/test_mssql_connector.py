from databricks.labs.lakebridge.connections.database_manager import MSSQLConnector


def test_mssql_connector_connection(sandbox_sqlserver):
    assert isinstance(sandbox_sqlserver.connector, MSSQLConnector)


def test_mssql_connector_execute_query(sandbox_sqlserver):
    # Test executing a query
    query = "SELECT 101 AS test_column"
    result = sandbox_sqlserver.fetch(query).rows
    assert result[0][0] == 101


def test_connection_test(sandbox_sqlserver):
    assert sandbox_sqlserver.check_connection()
