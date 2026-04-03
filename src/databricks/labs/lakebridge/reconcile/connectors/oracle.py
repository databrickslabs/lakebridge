import re
import logging
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


class OracleDataSource(DataSource):
    _IDENTIFIER_DELIMITER = "\""
    _SCHEMA_QUERY = """select column_name, case when (data_precision is not null
                                              and data_scale <> 0)
                                              then data_type || '(' || data_precision || ',' || data_scale || ')'
                                              when (data_precision is not null and data_scale = 0)
                                              then data_type || '(' || data_precision || ')'
                                              when data_precision is null and (lower(data_type) in ('date') or
                                              lower(data_type) like 'timestamp%') then  data_type
                                              when CHAR_LENGTH = 0 then data_type
                                              else data_type || '(' || CHAR_LENGTH || ')'
                                              end data_type
                                              FROM ALL_TAB_COLUMNS
                            WHERE lower(TABLE_NAME) = '{table}' and lower(owner) = '{owner}'"""

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
        query_opts = RemoteQueryReaderMixin.build_remote_query_options(catalog, "service_name", options)
        table_query = RemoteQueryReaderMixin.build_remote_query(
            self._connection_name, query_opts, query.replace(":tbl", f"{schema}.{table}"), "dbtable"
        )
        try:
            logger.warning(f"Fetching data using query: \n`{table_query}`")
            df = self._spark.sql(table_query)

            # Convert all column names to lower case
            df = df.select([col(c).alias(c.lower()) for c in df.columns])
            return df
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
            OracleDataSource._SCHEMA_QUERY.format(table=table.lower(), owner=schema.lower()),
        )
        query_opts = RemoteQueryReaderMixin.build_remote_query_options(catalog, "service_name")
        query = RemoteQueryReaderMixin.build_remote_query(self._connection_name, query_opts, schema_query, "query")
        try:
            logger.debug(f"Fetching schema using query: \n`{query}`")
            logger.debug(f"Fetching Schema: Started at: {datetime.now()}")
            df = self._spark.sql(query)
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.debug(f"Schema fetched successfully. Completed at: {datetime.now()}")
            logger.debug(f"schema_metadata: {schema_metadata}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        normalized = DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=OracleDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=OracleDataSource._IDENTIFIER_DELIMITER,
        )

        # TODO: In Oracle, quoted identifiers are case-sensitive,
        # it is disabled for now till we have a proper strategy to handle it.
        normalized.source_normalized = DialectUtils.unnormalize_identifier(normalized.ansi_normalized)

        return normalized
