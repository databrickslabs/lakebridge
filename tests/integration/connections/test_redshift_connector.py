from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, RedshiftConnector


def test_redshift_connector_connection(sandbox_redshift: DatabaseManager) -> None:
    """Test that Redshift DatabaseManager uses RedshiftConnector."""
    assert isinstance(sandbox_redshift.connector, RedshiftConnector)


def test_redshift_connector_execute_query(sandbox_redshift: DatabaseManager) -> None:
    """Test executing a query through Redshift DatabaseManager."""
    query = "SELECT 101 AS test_column"
    result = sandbox_redshift.fetch(query).rows
    assert result[0][0] == 101


def test_redshift_connection_check(sandbox_redshift: DatabaseManager) -> None:
    """Test connection check for Redshift."""
    assert sandbox_redshift.check_connection()
