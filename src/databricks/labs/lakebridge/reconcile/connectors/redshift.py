import re
import logging
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


class RedshiftDataSource(DataSource):
    _IDENTIFIER_DELIMITER = "\""
    _SCHEMA_QUERY = """SELECT
                         column_name,
                         CASE
                            WHEN data_type = 'numeric' AND numeric_precision IS NOT NULL
                                THEN 'decimal(' || numeric_precision || ',' || numeric_scale || ')'
                            WHEN data_type = 'character varying' AND character_maximum_length IS NOT NULL
                                THEN 'varchar(' || character_maximum_length || ')'
                            WHEN data_type = 'character' AND character_maximum_length IS NOT NULL
                                THEN 'char(' || character_maximum_length || ')'
                            WHEN data_type IN ('binary varying')
                                THEN 'binary'
                            ELSE data_type
                        END AS data_type
                        FROM
                            information_schema.columns
                        WHERE
                        LOWER(table_name) = LOWER('{table}')
                        AND LOWER(table_schema) = LOWER('{schema}')
                        ORDER BY ordinal_position
                  """

    def __init__(
        self,
        engine: Dialect,
        reader: RemoteQueryReader,
    ):
        self._engine = engine
        self._reader = reader

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        # Redshift dialect in SQLGlot converts :tbl to %(tbl)s (PostgreSQL parameter syntax)
        table_query = query.replace("%(tbl)s", f"{schema}.{table}").replace(":tbl", f"{schema}.{table}")
        try:
            logger.info(f"Fetching data using query: \n`{table_query}`")
            df = self._reader.read_data(table_query, catalog, "database", "query", options)
            return df.select([col(c).alias(c.lower()) for c in df.columns])
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
            RedshiftDataSource._SCHEMA_QUERY.format(schema=schema, table=table),
        )
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self._reader.read_data(schema_query, catalog, "database", "query")
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=RedshiftDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=RedshiftDataSource._IDENTIFIER_DELIMITER,
        )
