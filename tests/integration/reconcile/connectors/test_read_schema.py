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
from databricks.labs.lakebridge.reconcile.connectors.teradata import TeradataDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.debug_envgetter import TestEnvGetter


def test_sql_server_read_schema_happy(spark: SparkSession) -> None:
    connection = "sqlserver_sandbox"
    reader = RemoteQueryReader(spark, connection)
    connector = TSQLServerDataSource(get_dialect("tsql"), reader)

    columns = connector.get_schema("labs_azure_sandbox_remorph", "dbo", "reconcile_in")
    assert columns


def test_databricks_read_schema_happy(spark: SparkSession) -> None:
    # global_temp views are not in Unity Catalog's information_schema, so use the non-UC variant.
    connector = DatabricksNonUnityCatalogDataSource(get_dialect("databricks"), spark)
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
    connector = DatabricksDataSource(get_dialect("databricks"), spark)

    assert test_table.catalog_name
    assert test_table.schema_name
    assert test_table.name

    columns = connector.get_schema(test_table.catalog_name, test_table.schema_name, test_table.name)
    assert columns


def test_oracle_read_schema_happy(spark: SparkSession) -> None:
    connection = "oracle_sandbox"
    reader = RemoteQueryReader(spark, connection)
    connector = OracleDataSource(get_dialect("oracle"), reader)

    columns = connector.get_schema("orcl", "admin", "diamonds")
    assert columns


def test_oracle_catalog_read_schema_happy(spark: SparkSession) -> None:
    connector = DatabricksNonUnityCatalogDataSource(get_dialect("databricks"), spark)

    columns = connector.get_schema("oracle_sandbox_catalog", "admin", "diamonds")
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


def test_teradata_read_schema_happy(spark: SparkSession) -> None:
    connection = "teradata_sandbox"
    reader = RemoteQueryReader(spark, connection)
    connector = TeradataDataSource(get_dialect("teradata"), reader)

    columns = connector.get_schema("DBC", "lf_test_user", "diamonds")
    assert columns
