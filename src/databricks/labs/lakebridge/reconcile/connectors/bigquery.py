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
    """BigQuery data source read through a Databricks Lakehouse Federation UC connection.

    Data is fetched via the `remote_query` table-valued function (same path as Snowflake/Oracle/etc.),
    so credentials live in the UC connection and no JDBC driver is required here.

    Naming/quoting follows GoogleSQL: identifiers are backtick-quoted and tables are referenced
    two-part as `dataset.table` (dataset == schema), the same way the other federated connectors
    keep the top-level container out of the dotted name. The project is abstracted by the UC
    connection (its default project scopes unqualified names), so the `catalog` argument is unused.

    The `_SCHEMA_QUERY` `CASE` is the *Stage-1* type canonicalization for schema reconciliation: it
    emits, for the handful of BigQuery types that sqlglot cannot bridge to Databricks on its own, the
    Databricks-equivalent type string so the downstream `schema_compare` round-trip matches. Targets are
    taken from the empirically-tested BigQuery -> Databricks type mapping (FE GCP + DBSQL 2026.10):
      * BIGNUMERIC -> string  (precision 76 exceeds Databricks DECIMAL max 38; STRING preserves it)
      * NUMERIC    -> decimal(38,9)  (bare NUMERIC is fixed 38/9; sqlglot would emit DECIMAL(10,0))
      * TIME       -> string  (Databricks has no TIME type)
      * JSON       -> variant
      * RANGE<T>   -> struct<start <T>, end <T>>
    All other types (INT64, FLOAT64, BOOL, STRING, BYTES, DATE, DATETIME, TIMESTAMP, NUMERIC(p,s),
    GEOGRAPHY, ARRAY, STRUCT) are left raw because sqlglot translates them correctly.
    INTERVAL is intentionally not mapped: it migrates to two Databricks columns, which the one-to-one
    schema comparison cannot represent, so such columns surface as a visible mismatch.
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

    def __init__(
        self,
        engine: Dialect,
        reader: RemoteQueryReader,
        materialization_dataset: str | None = None,
    ):
        self._engine = engine
        self._reader = reader
        # BigQuery's remote_query rejects `database`; a `query` push requires `materializationDataset`
        # (a writable BigQuery dataset where results are materialized). Defaults to the dataset being
        # read; set explicitly when the source dataset is read-only for the connection's service account.
        self._materialization_dataset = materialization_dataset

    def _mat_dataset(self, schema: str) -> str:
        return self._materialization_dataset or schema

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        # sqlglot's BigQuery generator renders the `:tbl` placeholder as `@tbl` (BigQuery parameter
        # syntax), so substitute both forms — `@tbl` for builder-generated queries, `:tbl` for any
        # raw query string still using the convention.
        table_ref = f"`{schema}`.`{table}`"
        table_query = query.replace(":tbl", table_ref).replace("@tbl", table_ref)
        try:
            logger.info(f"Fetching data using query: \n`{table_query}`")
            df = self._reader.read_data(
                table_query, self._mat_dataset(schema), "materializationDataset", "query", options
            )
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
            df = self._reader.read_data(schema_query, self._mat_dataset(schema), "materializationDataset", "query")
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def list_schemas(self, catalog: str) -> list[str]:
        # SCHEMATA is project-level: the connection's default project scopes it (no project prefix).
        # It is not tied to a single dataset, so the discovery path needs an explicit
        # materialization_dataset configured on the connector for the remote_query pushdown.
        query = BigQueryDataSource._LIST_SCHEMAS_QUERY
        try:
            df = self._reader.read_data(query, self._mat_dataset(""), "materializationDataset", "query")
            return [row.schema_name for row in df.select(col("schema_name").alias("schema_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schemas", query)

    def list_tables(self, catalog: str, schema: str) -> list[str]:
        query = BigQueryDataSource._LIST_TABLES_QUERY.format(schema=schema)
        try:
            df = self._reader.read_data(query, self._mat_dataset(schema), "materializationDataset", "query")
            return [row.table_name for row in df.select(col("table_name").alias("table_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "tables", query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
        )
