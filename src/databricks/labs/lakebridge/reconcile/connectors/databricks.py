import logging
import re
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


def _get_describe_query(catalog: str, schema: str, table: str):
    if schema == "global_temp":
        return f"describe table global_temp.{table}"
    return f"describe table {catalog}.{schema}.{table}"


def _get_information_schema_query(catalog: str, schema: str, table: str):
    query = f"""select
                            lower(column_name) as col_name,
                             full_data_type as data_type
                       from {catalog}.information_schema.columns
                       where lower(table_catalog)='{catalog}'
                                    and lower(table_schema)='{schema}'
                                     and lower(table_name) ='{table}'
                       order by col_name"""
    return re.sub(r'\s+', ' ', query)


class DatabricksDataSource(DataSource):
    """Databricks data source backed by Unity Catalog `information_schema`.

    Use `DatabricksNonUnityCatalogDataSource` for hive_metastore, global views,
    or Foreign Catalogs
    """

    _IDENTIFIER_DELIMITER = "`"

    def __init__(
        self,
        engine: Dialect,
        spark: SparkSession,
        ws: WorkspaceClient,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        if schema == "global_temp":
            namespace_catalog = "global_temp"
        else:
            namespace_catalog = f"{catalog}.{schema}"
        table_with_namespace = f"{namespace_catalog}.{table}"
        table_query = query.replace(":tbl", table_with_namespace)
        try:
            df = self._spark.sql(table_query)
            return df.select([col(column).alias(column.lower()) for column in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", table_query)

    def get_schema(
        self,
        catalog: str,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        schema_query = _get_information_schema_query(catalog, schema, table)
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            schema_metadata = (
                self._spark.sql(schema_query)
                .selectExpr("col_name as column_name", "data_type")
                .where("column_name not like '#%'")
                .distinct()
                .collect()
            )
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=DatabricksDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=DatabricksDataSource._IDENTIFIER_DELIMITER,
        )


class DatabricksNonUnityCatalogDataSource(DatabricksDataSource):

    def get_schema(
        self,
        catalog: str,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        schema_query = _get_describe_query(catalog, schema, table)
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            schema_metadata = (
                self._spark.sql(schema_query)
                .selectExpr("col_name as column_name", "data_type")
                .where("column_name not like '#%'")
                .distinct()
                .collect()
            )
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)
