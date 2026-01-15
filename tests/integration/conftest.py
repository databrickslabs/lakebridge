import logging
from urllib.parse import urlparse

import pytest
from pyspark.sql import SparkSession

from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from tests.integration.debug_envgetter import TestEnvGetter

logging.getLogger("tests").setLevel("DEBUG")
logging.getLogger("databricks.labs.lakebridge").setLevel("DEBUG")

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
def sandbox_sqlserver_config() -> dict:
    env = TestEnvGetter(True)
    db_url = env.get("TEST_TSQL_JDBC").removeprefix("jdbc:")
    base_url, params = db_url.split(";", 1)
    url_parts = urlparse(base_url)
    server = url_parts.hostname
    query_params = dict(param.split("=", 1) for param in params.split(";") if "=" in param)
    database = query_params.get("database", "")

    config = {
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
