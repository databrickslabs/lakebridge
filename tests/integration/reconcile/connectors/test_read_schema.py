from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

from databricks.sdk import WorkspaceClient

from tests.integration.debug_envgetter import TestEnvGetter


class TSQLServerDataSourceUnderTest(TSQLServerDataSource):
    def __init__(self, engine, spark, ws, secret_scope):
        super().__init__(engine, spark, ws, secret_scope)
        self._test_env = TestEnvGetter(True)

    @property
    def get_jdbc_url(self) -> str:
        return (
            self._test_env.get("TEST_TSQL_JDBC")
            + f"user={self._test_env.get('TEST_TSQL_USER')};"
            + f"password={self._test_env.get('TEST_TSQL_PASS')};"
        )


@pytest.mark.skip(reason="Add the creds to Github secrets and populate the actions' env to enable this test")
def test_tsql_server_read_schema_happy(mock_spark):
    mock_ws = create_autospec(WorkspaceClient)
    connector = TSQLServerDataSourceUnderTest(get_dialect("tsql"), mock_spark, mock_ws, "my_secret")

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
