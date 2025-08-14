from databricks.labs.lakebridge.connections.database_manager import MSSQLConnector


def test_mssql_connector_connection(test_sqlserver):
    assert isinstance(test_sqlserver.connector, MSSQLConnector)


def test_mssql_connector_execute_query(test_sqlserver):
    # Test executing a query
    query = "SELECT 101 AS test_column"
    result = test_sqlserver.execute_query(query)
    row = result.fetchone()
    assert row[0] == 101


def test_connection_test(test_sqlserver):
    assert test_sqlserver.check_connection()
