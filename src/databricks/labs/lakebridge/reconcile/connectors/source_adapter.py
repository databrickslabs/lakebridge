from pyspark.sql import SparkSession
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksSourceDataSource, DatabricksTargetDataSource
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.oracle import Oracle
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.tsql import Tsql
from databricks.sdk import WorkspaceClient


def create_adapter(
    engine: Dialect,
    spark: SparkSession,
    ws: WorkspaceClient,
    secret_scope: str,
    is_target: bool = False,
) -> DataSource:
    if isinstance(engine, Snowflake):
        return SnowflakeDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, Oracle):
        return OracleDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, Databricks):
        if is_target:
            return DatabricksTargetDataSource(engine, spark, ws, secret_scope)
        return DatabricksSourceDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, Tsql):
        return TSQLServerDataSource(engine, spark, ws, secret_scope)
    raise ValueError(f"Unsupported source type --> {engine}")
