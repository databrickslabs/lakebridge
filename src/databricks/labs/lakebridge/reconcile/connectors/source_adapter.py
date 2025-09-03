from pyspark.sql import SparkSession
from sqlglot import Dialect
from sqlglot.dialects import TSQL

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.oracle import Oracle
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake
from databricks.sdk import WorkspaceClient


def create_adapter(
    engine: Dialect,
    spark: SparkSession,
    ws: WorkspaceClient,
    secret_scope: str,
) -> DataSource:
    if isinstance(engine, Snowflake):
        return SnowflakeDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, Oracle):
        return OracleDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, Databricks):
        return DatabricksDataSource(engine, spark, ws, secret_scope)
    if isinstance(engine, TSQL):
        return TSQLServerDataSource(engine, spark, ws, secret_scope)
    raise ValueError(f"Unsupported source type --> {engine}")
