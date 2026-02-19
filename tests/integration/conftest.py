import logging
from urllib.parse import urlparse
from uuid import UUID

import pytest
from pyspark.sql import SparkSession

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from tests.integration.debug_envgetter import TestEnvGetter

logging.getLogger("tests").setLevel(logging.DEBUG)
logging.getLogger("databricks.labs.lakebridge").setLevel(logging.DEBUG)
logging.getLogger("databricks.labs.pytester").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


@pytest.fixture
def debug_env_name():
    return "ucws"


@pytest.fixture
def product_info() -> tuple[str, str]:
    return "lakebridge-integration-tests", __version__


@pytest.fixture
def get_logger():
    return logger


@pytest.fixture(scope="session")
def mock_spark() -> SparkSession:
    """
    Method helps to create spark session
    :return: returns the spark session
    """
    return SparkSession.builder.appName("Remorph Reconcile Test").remote("sc://localhost").getOrCreate()


@pytest.fixture()
def sandbox_sqlserver_config() -> JsonObject:
    env = TestEnvGetter(True)
    db_url = env.get("TEST_TSQL_JDBC").removeprefix("jdbc:")
    base_url, params = db_url.split(";", 1)
    url_parts = urlparse(base_url)
    server = url_parts.hostname
    query_params = dict(param.split("=", 1) for param in params.split(";") if "=" in param)
    database = query_params.get("database", "")

    config: JsonObject = {
        "user": env.get("TEST_TSQL_USER"),
        "password": env.get("TEST_TSQL_PASS"),
        "server": server,
        "database": database,
        "driver": "ODBC Driver 18 for SQL Server",
    }
    return config


@pytest.fixture()
def sandbox_sqlserver(sandbox_sqlserver_config) -> DatabaseManager:
    return DatabaseManager("mssql", sandbox_sqlserver_config)


@pytest.fixture()
def sandbox_synapse_config(sandbox_sqlserver_config: JsonObject) -> JsonObject:
    """Convert SQL Server config to Synapse config format for direct DatabaseManager usage."""
    # Transform MSSQL config to Synapse format
    # In testing, we use SQL Server as a stand-in for Synapse since they use the same protocol
    return {
        "server": sandbox_sqlserver_config["server"],
        "user": sandbox_sqlserver_config["user"],
        "password": sandbox_sqlserver_config["password"],
        "driver": sandbox_sqlserver_config["driver"],
        "database": sandbox_sqlserver_config["database"],
        "auth_type": "sql_authentication",
        "port": 1433,
    }


@pytest.fixture()
def sandbox_synapse_cred_config(sandbox_sqlserver_config: JsonObject) -> JsonObject:
    """Create complete Synapse credential structure as stored by configure-database-profiler.

    This mimics the full structure returned by credential manager for Synapse,
    matching the format in .credentials.yml after running 'databricks labs lakebridge configure-database-profiler'.
    """
    server = sandbox_sqlserver_config["server"]
    assert isinstance(server, str)
    # Extract workspace name from server (e.g., "workspace-name" from "workspace-name.sql.azuresynapse.net")
    workspace_name = server.split('.')[0] if '.' in server else server

    return {
        "secret_vault_type": "local",
        "secret_vault_name": None,
        "synapse": {
            "workspace": {
                "name": workspace_name,
                "dedicated_sql_endpoint": server,
                "serverless_sql_endpoint": f"{workspace_name}-ondemand.sql.azuresynapse.net",
                "sql_user": sandbox_sqlserver_config["user"],
                "sql_password": sandbox_sqlserver_config["password"],
                "driver": sandbox_sqlserver_config["driver"],
                "tz_info": "UTC",
            },
            "azure_api_access": {
                "development_endpoint": f"https://{workspace_name}.dev.azuresynapse.net",
            },
            "jdbc": {
                "auth_type": "sql_authentication",
                "fetch_size": "1000",
                "login_timeout": "30",
            },
            "profiler": {
                "exclude_serverless_sql_pool": False,
                "exclude_dedicated_sql_pools": False,
                "exclude_spark_pools": False,
                "exclude_monitoring_metrics": False,
                "redact_sql_pools_sql_text": False,
                "databases": sandbox_sqlserver_config["database"],
            },
        },
    }


@pytest.fixture()
def sandbox_synapse(sandbox_synapse_config: JsonObject) -> DatabaseManager:
    """Create a DatabaseManager for Synapse (uses MSSQLConnector via factory method)."""
    return DatabaseManager("synapse", sandbox_synapse_config)


@pytest.fixture()
def recon_id() -> UUID:
    return UUID("00112233-4455-6677-8899-aabbccddeeff")
