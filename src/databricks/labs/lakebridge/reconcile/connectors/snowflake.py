import logging
import re
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReaderMixin
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class SnowflakeDataSource(DataSource):
    _DRIVER = "snowflake"
    _IDENTIFIER_DELIMITER = "\""

    """
       * INFORMATION_SCHEMA:
          - see https://docs.snowflake.com/en/sql-reference/info-schema#considerations-for-replacing-show-commands-with-information-schema-views
       * DATA:
          - only unquoted identifiers are treated as case-insensitive and are stored in uppercase.
          - for quoted identifiers refer:
             https://docs.snowflake.com/en/sql-reference/identifiers-syntax#double-quoted-identifiers
       * ORDINAL_POSITION:
          - indicates the sequential order of a column within a table or view,
             starting from 1 based on the order of column definition.
    """
    _SCHEMA_QUERY = """select column_name,
                                                      case
                                                            when numeric_precision is not null and numeric_scale is not null
                                                            then
                                                                concat(data_type, '(', numeric_precision, ',' , numeric_scale, ')')
                                                            when lower(data_type) = 'text'
                                                            then
                                                                concat('varchar', '(', CHARACTER_MAXIMUM_LENGTH, ')')
                                                            else data_type
                                                      end as data_type
                                                      from {catalog}.INFORMATION_SCHEMA.COLUMNS
                                                      where lower(table_name)='{table}' and table_schema = '{schema}'
                                                      order by ordinal_position"""

    def __init__(
        self,
        engine: Dialect,
        spark: SparkSession,
        ws: WorkspaceClient,
        connection_name: str,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._connection_name = connection_name

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        table_query = query.replace(":tbl", f"{catalog}.{schema}.{table}")
        query = self._build_query(table_query, catalog)
        try:
            logger.info(f"Fetching data using query: \n`{query}`")
            df = self._spark.sql(query)
            return df.select([col(column).alias(column.lower()) for column in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", query)

    def get_schema(
        self,
        catalog: str,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        """
        Fetch the Schema from the INFORMATION_SCHEMA.COLUMNS table in Snowflake.

        If the user's current role does not have the necessary privileges to access the specified
        Information Schema object, RunTimeError will be raised:
        "SQL access control error: Insufficient privileges to operate on schema 'INFORMATION_SCHEMA' "
        """
        schema_query = re.sub(
            r'\s+',
            ' ',
            SnowflakeDataSource._SCHEMA_QUERY.format(catalog=catalog, schema=schema.upper(), table=table),
        )
        query = self._build_query(f"({schema_query}) as tmp", catalog)
        try:
            logger.info(f"Fetching schema using query: \n`{query}`")
            logger.debug(f"Fetching Schema: Started at: {datetime.now()}")
            df = self._spark.sql(query)
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.debug(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", query)

    def _build_query(self, source_query: str, catalog: str) -> str:
        query_opts = RemoteQueryReaderMixin.build_remote_query_options(catalog, "database", None)
        return RemoteQueryReaderMixin.build_remote_query(self._connection_name, query_opts, source_query, "dbtable")

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        normalized = DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=SnowflakeDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=SnowflakeDataSource._IDENTIFIER_DELIMITER,
        )

        # TODO: In Snowflake, quoted identifiers are case-sensitive,
        # it is disabled for now till we have a proper strategy to handle it.
        normalized.source_normalized = DialectUtils.unnormalize_identifier(normalized.ansi_normalized)

        return normalized
