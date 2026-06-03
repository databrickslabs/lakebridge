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


class TeradataDataSource(DataSource):
    _IDENTIFIER_DELIMITER = "\""
    _SCHEMA_QUERY = """SELECT
                        TRIM(ColumnName) AS column_name,
                        CASE
                            WHEN ColumnType IN ('I')  THEN 'integer'
                            WHEN ColumnType IN ('I1') THEN 'byteint'
                            WHEN ColumnType IN ('I2') THEN 'smallint'
                            WHEN ColumnType IN ('I8') THEN 'bigint'
                            WHEN ColumnType IN ('D')  THEN
                                'decimal(' || TRIM(CAST(DecimalTotalDigits AS VARCHAR(10))) || ','
                                || TRIM(CAST(DecimalFractionalDigits AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('F')  THEN 'double precision'
                            WHEN ColumnType IN ('CF') THEN 'char(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('CV') THEN 'varchar(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('DA') THEN 'date'
                            WHEN ColumnType IN ('AT') THEN 'time'
                            WHEN ColumnType IN ('TS') THEN 'timestamp'
                            WHEN ColumnType IN ('TZ') THEN 'time with time zone'
                            WHEN ColumnType IN ('SZ') THEN 'timestamp with time zone'
                            WHEN ColumnType IN ('BO') THEN 'blob'
                            WHEN ColumnType IN ('CO') THEN 'clob'
                            WHEN ColumnType IN ('BF') THEN 'byte(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('BV') THEN 'varbyte(' || TRIM(CAST(ColumnLength AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('N')  THEN
                                'number(' || TRIM(CAST(DecimalTotalDigits AS VARCHAR(10))) || ','
                                || TRIM(CAST(DecimalFractionalDigits AS VARCHAR(10))) || ')'
                            WHEN ColumnType IN ('JN') THEN 'json'
                            WHEN ColumnType IN ('XM') THEN 'xml'
                            ELSE LOWER(TRIM(ColumnType))
                        END AS data_type
                        FROM DBC.ColumnsV
                        WHERE LOWER(TableName) = LOWER('{table}')
                        AND LOWER(DatabaseName) = LOWER('{schema}')
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
        table_query = query.replace(":tbl", f"{schema}.{table}")
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
            TeradataDataSource._SCHEMA_QUERY.format(schema=schema, table=table),
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
            source_start_delimiter=TeradataDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=TeradataDataSource._IDENTIFIER_DELIMITER,
        )
