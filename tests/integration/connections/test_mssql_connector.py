from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.connections.database_manager import create_connector, MSSQLConnector


def test_mssql_connector_connection(sandbox_sqlserver):
    assert isinstance(sandbox_sqlserver, MSSQLConnector)


def test_mssql_connector_execute_query(sandbox_sqlserver):
    # Test executing a query
    query = "SELECT 101 AS test_column"
    result = sandbox_sqlserver.fetch(query).rows
    assert result[0][0] == 101


def test_connection_test(sandbox_sqlserver):
    assert sandbox_sqlserver.health_check()


def test_spn_authentication_connection(sandbox_spn_sqlserver_config: JsonObject) -> None:
    connector = create_connector("mssql", sandbox_spn_sqlserver_config)
    assert connector.health_check()
