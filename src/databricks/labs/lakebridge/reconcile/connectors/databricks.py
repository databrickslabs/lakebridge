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
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        namespace_catalog = "hive_metastore" if not catalog else catalog
        if schema == "global_temp":
            namespace_catalog = "global_temp"
        else:
            namespace_catalog = f"{namespace_catalog}.{schema}"
        table_with_namespace = f"{namespace_catalog}.{table}"
        table_query = query.replace(":tbl", table_with_namespace)
        try:
            df = self._spark.sql(table_query)
            return df.select([col(column).alias(column.lower()) for column in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", table_query)

    def _fetch_schema(self, schema_query: str, normalize: bool) -> list[Schema]:
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

    def get_schema(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        raise NotImplementedError("Use DatabricksSourceDataSource or DatabricksTargetDataSource")

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=DatabricksDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=DatabricksDataSource._IDENTIFIER_DELIMITER,
        )


class DatabricksSourceDataSource(DatabricksDataSource):
    """Databricks data source for the source side of reconciliation.

    Uses DESCRIBE TABLE for schema fetching, which works for hive_metastore,
    global_temp views, and Foreign Catalogs (Lakehouse Federation).
    """

    def get_schema(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        catalog_str = catalog if catalog else "hive_metastore"
        schema_query = _get_describe_query(catalog_str, schema, table)
        try:
            return self._fetch_schema(schema_query, normalize)
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)


class DatabricksTargetDataSource(DatabricksDataSource):
    """Databricks data source for the target side of reconciliation.

    Uses information_schema.columns with full_data_type for schema fetching,
    which provides precise type information for native Unity Catalog tables.
    """

    def get_schema(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        catalog_str = catalog if catalog else "hive_metastore"
        if catalog_str == "hive_metastore" or schema == "global_temp":
            schema_query = _get_describe_query(catalog_str, schema, table)
        else:
            schema_query = _get_information_schema_query(catalog_str, schema, table)
        try:
            return self._fetch_schema(schema_query, normalize)
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)
