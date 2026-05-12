from pyspark.sql import SparkSession
from sqlglot import Dialect
from sqlglot.dialects.redshift import Redshift

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.databricks import (
    DatabricksDataSource,
    DatabricksNonUnityCatalogDataSource,
)
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.connectors.snowflake import SnowflakeDataSource
from databricks.labs.lakebridge.reconcile.connectors.tsql import TSQLServerDataSource
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.oracle import Oracle
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.tsql import Tsql
from databricks.sdk import WorkspaceClient


# TODO add checks connection exists
def create_adapter(
    engine: Dialect,
    spark: SparkSession,
    ws: WorkspaceClient,
    connection_name: str,
    is_target: bool = False,
) -> DataSource:
    reader = RemoteQueryReader(spark, connection_name)
    if isinstance(engine, Snowflake):
        return SnowflakeDataSource(engine, reader)
    if isinstance(engine, Oracle):
        return OracleDataSource(engine, reader)
    if isinstance(engine, Databricks):
        if is_target:
            return DatabricksDataSource(engine, spark, ws)
        return DatabricksNonUnityCatalogDataSource(engine, spark, ws)
    if isinstance(engine, Tsql):
        return TSQLServerDataSource(engine, reader)
    if isinstance(engine, Redshift):
        return RedshiftDataSource(engine, reader)
    raise ValueError(f"Unsupported source type --> {engine}")
