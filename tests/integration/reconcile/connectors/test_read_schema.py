from unittest.mock import create_autospec

import pytest

from pyspark.sql import DataFrameReader

from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

from databricks.sdk import WorkspaceClient

from tests.integration.debug_envgetter import TestEnvGetter


class TSQLServerDataSourceUnderTest(TSQLServerDataSource):
    def __init__(self, spark, ws):
        super().__init__(get_dialect("tsql"), spark, ws, "secret_scope")
        self._test_env = TestEnvGetter(True)

    @property
    def get_jdbc_url(self) -> str:
        return (
            self._test_env.get("TEST_TSQL_JDBC")
            + f"user={self._test_env.get('TEST_TSQL_USER')};"
            + f"password={self._test_env.get('TEST_TSQL_PASS')};"
        )

class OracleDataSourceUnderTest(OracleDataSource):
    def __init__(self, spark, ws):
        super().__init__(get_dialect("oracle"), spark, ws, "secret_scope")
        self._test_env = TestEnvGetter(True)

    @property
    def get_jdbc_url(self) -> str:
        return "jdbc:oracle:thin:@//HOST:PORT/SERVICE"  # FIXME

    def reader(self, query: str) -> DataFrameReader:
        user = "FIXME"
        password = "FIXME"
        return self._get_jdbc_reader(query, self.get_jdbc_url, OracleDataSource._DRIVER, {"user": user, "password": password})


class SnowflakeDataSourceUnderTest(SnowflakeDataSource):
    def __init__(self, spark, ws):
        super().__init__(get_dialect("snowflake"), spark, ws, "secret_scope")
        self._test_env = TestEnvGetter(True)

    @property
    def get_jdbc_url(self) -> str:
        raise NotImplementedError("Not needed for this test")

    def reader(self, query: str) -> DataFrameReader:
        options = self._get_snowflake_options()
        return self._spark.read.format("snowflake").option("dbtable", f"({query}) as tmp").options(**options)

    def _get_snowflake_options(self):
        return {
            "sfURL": "FIXME",
            "sfUser": "FIXME",
            "sfDatabase": "FIXME",
            "sfSchema": "FIXME",
            "sfWarehouse": "FIXME",
            "sfRole": "FIXME",
            "pem_private_key": SnowflakeDataSource._get_private_key(self._test_env.get("TEST_SNOWFLAKE_PRIVATE_KEY"), None)
        }


@pytest.mark.skip(reason="Add the creds to Github secrets and populate the actions' env to enable this test")
def test_sql_server_read_schema_happy(mock_spark):
    mock_ws = create_autospec(WorkspaceClient)
    connector = TSQLServerDataSourceUnderTest(mock_spark, mock_ws)

    columns = connector.get_schema("labs_azure_sandbox_remorph", "dbo", "Employees")
    assert columns


def test_databricks_read_schema_happy(mock_spark):
    mock_ws = create_autospec(WorkspaceClient)
    connector = DatabricksDataSource(get_dialect("databricks"), mock_spark, mock_ws, "my_secret")

    mock_spark.sql("CREATE DATABASE IF NOT EXISTS my_test_db")
    mock_spark.sql("CREATE TABLE IF NOT EXISTS my_test_db.my_test_table (id INT, name STRING) USING parquet")
    df = mock_spark.sql("SELECT * FROM my_test_db.my_test_table")
    df.createGlobalTempView("my_global_test_view")
    columns = connector.get_schema(None, "global_temp", "my_global_test_view")
    assert columns

# FIXME
# 1. Deploy Oracle Free
# 2. Add credentials to the test env getter
@pytest.mark.skip(reason="Not Ready! Deploy Infra")
def test_oracle_read_schema_happy(mock_spark):
    mock_ws = create_autospec(WorkspaceClient)
    connector = OracleDataSourceUnderTest(mock_spark, mock_ws)

    columns = connector.get_schema(None, "SYSTEM", "help")
    assert columns

# FIXME the test pem key does not have access to LABS schema as it should
@pytest.mark.skip(reason="Add the creds to Github secrets and populate the actions' env to enable this test")
def test_snowflake_read_schema_happy(mock_spark):
    mock_ws = create_autospec(WorkspaceClient)
    connector = SnowflakeDataSourceUnderTest(mock_spark, mock_ws)

    columns = connector.get_schema('"sandbox"', "LABS", "diamonds")
    assert columns
