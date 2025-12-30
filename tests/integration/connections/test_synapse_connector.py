import pytest
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, SynapseConnector
from databricks.labs.lakebridge.resources.assessments.synapse.common.connector import get_sqlpool_reader


@pytest.fixture()
def sandbox_synapse_config(sandbox_sqlserver_config) -> dict:
    """Convert SQL Server config to Synapse config format."""
    # Transform MSSQL config to Synapse format
    # In testing, we use SQL Server as a stand-in for Synapse since they use the same protocol
    return {
        "dedicated_sql_endpoint": sandbox_sqlserver_config["server"],
        "sql_user": sandbox_sqlserver_config["user"],
        "sql_password": sandbox_sqlserver_config["password"],
        "driver": sandbox_sqlserver_config["driver"],
        "database": sandbox_sqlserver_config["database"],
    }


@pytest.fixture()
def sandbox_synapse(sandbox_synapse_config) -> DatabaseManager:
    """Create a DatabaseManager using SynapseConnector."""
    return DatabaseManager("synapse", sandbox_synapse_config)


def test_synapse_connector_connection(sandbox_synapse):
    """Test that SynapseConnector can be instantiated."""
    assert isinstance(sandbox_synapse.connector, SynapseConnector)


def test_synapse_connector_execute_query(sandbox_synapse):
    """Test executing a query through SynapseConnector."""
    query = "SELECT 101 AS test_column"
    result = sandbox_synapse.fetch(query).rows
    assert result[0][0] == 101


def test_synapse_connection_check(sandbox_synapse):
    """Test connection check through SynapseConnector."""
    assert sandbox_synapse.check_connection()


def test_get_sqlpool_reader_dedicated(sandbox_synapse_config):
    """Test get_sqlpool_reader with dedicated endpoint."""
    db_name = sandbox_synapse_config["database"]

    manager = get_sqlpool_reader(
        sandbox_synapse_config,
        db_name,
        endpoint_key='dedicated_sql_endpoint',
        auth_type='sql_authentication',
    )

    assert isinstance(manager, DatabaseManager)
    assert isinstance(manager.connector, SynapseConnector)
    assert manager.check_connection()


def test_get_sqlpool_reader_query(sandbox_synapse_config):
    """Test get_sqlpool_reader can execute queries."""
    db_name = sandbox_synapse_config["database"]

    manager = get_sqlpool_reader(
        sandbox_synapse_config,
        db_name,
        endpoint_key='dedicated_sql_endpoint',
    )

    query = "SELECT 202 AS test_column"
    result = manager.fetch(query).rows
    assert result[0][0] == 202
