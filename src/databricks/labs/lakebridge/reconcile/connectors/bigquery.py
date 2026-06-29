import logging
import re
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame
from pyspark.sql.functions import col
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema

logger = logging.getLogger(__name__)


class BigQueryDataSource(DataSource):
    """BigQuery source read through a Databricks Lakehouse Federation UC connection.

    Tables are referenced two-part as ``dataset.table``; the project comes from the UC connection.
    ``catalog`` is used as the ``remote_query`` ``materializationDataset`` and must be a BigQuery
    dataset writable by the connection's service account.
    """

    _IDENTIFIER_DELIMITER = "`"

    _LIST_SCHEMAS_QUERY = "select schema_name from INFORMATION_SCHEMA.SCHEMATA order by schema_name"
    _LIST_TABLES_QUERY = "select table_name from `{schema}`.INFORMATION_SCHEMA.TABLES order by table_name"
    _SCHEMA_QUERY = """select column_name,
                                  case
                                        when data_type like 'BIGNUMERIC%' then 'string'
                                        when data_type = 'NUMERIC' then 'decimal(38,9)'
                                        when data_type = 'TIME' then 'string'
                                        when data_type = 'JSON' then 'variant'
                                        when data_type = 'RANGE<DATE>' then 'struct<start date, end date>'
                                        when data_type = 'RANGE<DATETIME>'
                                            then 'struct<start timestamp_ntz, end timestamp_ntz>'
                                        when data_type = 'RANGE<TIMESTAMP>'
                                            then 'struct<start timestamp, end timestamp>'
                                        else data_type
                                  end as data_type
                                  from `{schema}`.INFORMATION_SCHEMA.COLUMNS
                                  where table_name = '{table}'
                                  order by ordinal_position"""

    def __init__(self, engine: Dialect, reader: RemoteQueryReader):
        self._engine = engine
        self._reader = reader

    def _read(self, query: str, materialization_dataset: str) -> DataFrame:
        return self._reader.read_data_direct(query, "query", {"materializationDataset": materialization_dataset})

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        table_ref = f"`{catalog}.{schema}.{table}`"
        table_query = query.replace(":tbl", table_ref).replace("@tbl", table_ref)
        try:
            logger.info(f"Fetching data using query: \n`{table_query}`")
            df = self._read(table_query, schema)
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
        schema_query = re.sub(
            r'\s+',
            ' ',
            BigQueryDataSource._SCHEMA_QUERY.format(schema=schema, table=table),
        )
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self._read(schema_query, schema)
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def list_schemas(self, catalog: str) -> list[str]:
        query = BigQueryDataSource._LIST_SCHEMAS_QUERY
        try:
            df = self._read(query, catalog)  # User has to create a dataset with the value
            return [row.schema_name for row in df.select(col("schema_name").alias("schema_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schemas", query)

    def list_tables(self, catalog: str, schema: str) -> list[str]:
        query = BigQueryDataSource._LIST_TABLES_QUERY.format(schema=schema)
        try:
            df = self._read(query, schema)
            return [row.table_name for row in df.select(col("table_name").alias("table_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "tables", query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
        )
