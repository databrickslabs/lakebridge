from unittest.mock import create_autospec
import uuid
import pytest

from pyspark.sql import SparkSession
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import TableInfo
from databricks.labs.lakebridge.reconcile.connectors.databricks import (
    DatabricksDataSource,
    DatabricksNonUnityCatalogDataSource,
)
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.test_oracle_reconcile import OracleDataSourceUnderTest
from tests.integration.debug_envgetter import TestEnvGetter


def test_sql_server_read_schema_happy(spark: SparkSession) -> None:
    connection = "sqlserver_sandbox"
    reader = RemoteQueryReader(spark, connection)
    connector = TSQLServerDataSource(get_dialect("tsql"), reader)

    columns = connector.get_schema("labs_azure_sandbox_remorph", "dbo", "reconcile_in")
    assert columns


def test_databricks_read_schema_happy(spark: SparkSession) -> None:
    mock_ws = create_autospec(WorkspaceClient)
    # global_temp views are not in Unity Catalog's information_schema, so use the non-UC variant.
    connector = DatabricksNonUnityCatalogDataSource(get_dialect("databricks"), spark, mock_ws)
    random_view = f"test_view_{uuid.uuid4().hex}"

    try:
        spark.sql("CREATE DATABASE IF NOT EXISTS my_test_db")
        spark.sql("CREATE TABLE IF NOT EXISTS my_test_db.my_test_table (id INT, name STRING) USING parquet")
        df = spark.sql("SELECT * FROM my_test_db.my_test_table")
        df.createGlobalTempView(random_view)
        # global_temp short-circuits the catalog, so the value here is ignored by the DESCRIBE query.
        columns = connector.get_schema("hive_metastore", "global_temp", random_view)

        assert columns
    finally:
        assert spark.catalog.dropGlobalTempView(random_view)


def test_databricks_read_schema_happy_sandbox(
    spark: SparkSession, ws: WorkspaceClient, recon_tables: tuple[TableInfo, TableInfo]
) -> None:
    test_table, _ = recon_tables
    connector = DatabricksDataSource(get_dialect("databricks"), spark, ws)

    assert test_table.catalog_name
    assert test_table.schema_name
    assert test_table.name

    columns = connector.get_schema(test_table.catalog_name, test_table.schema_name, test_table.name)
    assert columns


# FIXME
# 1. Deploy Oracle Free
# 2. Add credentials to the test env getter
@pytest.mark.skip(reason="Not Ready! Deploy Infra")
def test_oracle_read_schema_happy(spark: SparkSession) -> None:
    connector = OracleDataSourceUnderTest(spark)

    columns = connector.get_schema("ORCL", "SYSTEM", "help")
    assert columns


def test_redshift_read_schema_happy(spark: SparkSession) -> None:
    connection = "sandbox_labs_tool_redshift"
    reader = RemoteQueryReader(spark, connection)
    connector = RedshiftDataSource(get_dialect("redshift"), reader)

    columns = connector.get_schema("labs", "lakebridge", "diamonds")
    assert columns


@pytest.mark.xfail(reason="Snowflake account unavailable", strict=True)
def test_snowflake_read_schema_happy(spark: SparkSession) -> None:
    connection = TestEnvGetter(False).get("TEST_SNOWFLAKE_CONNECTION")
    reader = RemoteQueryReader(spark, connection)
    connector = SnowflakeDataSource(get_dialect("snowflake"), reader)

    columns = connector.get_schema('remorph', "sandbox", "diamonds")
    assert columns
