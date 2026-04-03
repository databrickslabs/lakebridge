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

_SCHEMA_QUERY = """SELECT
                     COLUMN_NAME AS 'column_name',
                     CASE
                        WHEN DATA_TYPE IN ('int', 'bigint')
                            THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('smallint', 'tinyint')
                            THEN 'smallint'
                        WHEN DATA_TYPE IN ('decimal' ,'numeric')
                            THEN 'decimal(' +
                                CAST(NUMERIC_PRECISION AS VARCHAR) + ',' +
                                CAST(NUMERIC_SCALE AS VARCHAR) + ')'
                        WHEN DATA_TYPE IN ('float', 'real')
                                THEN 'double'
                        WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL AND DATA_TYPE IN ('varchar','char','text','nchar','nvarchar','ntext')
                                THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('date','time','datetime', 'datetime2','smalldatetime','datetimeoffset')
                                THEN DATA_TYPE
                        WHEN DATA_TYPE IN ('bit')
                                THEN 'boolean'
                        WHEN DATA_TYPE IN ('binary','varbinary')
                                THEN 'binary'
                        ELSE DATA_TYPE
                    END AS 'data_type'
                    FROM
                        INFORMATION_SCHEMA.COLUMNS
                    WHERE
                    LOWER(TABLE_NAME) = LOWER('{table}')
                    AND LOWER(TABLE_SCHEMA) = LOWER('{schema}')
                    AND LOWER(TABLE_CATALOG) = LOWER('{catalog}')
              """


class TSQLServerDataSource(DataSource):
    _DRIVER = "sqlserver"
    _IDENTIFIER_DELIMITER = {"prefix": "[", "suffix": "]"}

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
        table_query = query.replace(":tbl", f"{schema}.{self.normalize_identifier(table).source_normalized}")
        query = self._build_query(table_query, catalog, options)
        try:
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
        Fetch the Schema from the INFORMATION_SCHEMA.COLUMNS table in SQL Server.

        If the user's current role does not have the necessary privileges to access the specified
        Information Schema object, RunTimeError will be raised:
        "SQL access control error: Insufficient privileges to operate on schema 'INFORMATION_SCHEMA' "
        """
        schema_query = re.sub(
            r'\s+',
            ' ',
            _SCHEMA_QUERY.format(catalog=catalog, schema=schema, table=table),
        )
        query = self._build_query(schema_query, catalog, None)
        try:
            logger.debug(f"Fetching schema using query: \n`{query}`")
            logger.debug(f"Fetching Schema: Started at: {datetime.now()}")
            df = self._spark.sql(query)
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.debug(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", query)

    def _build_query(
        self,
        source_query: str,
        catalog: str,
        options: JdbcReaderOptions | None,
    ) -> str:
        query_opts = RemoteQueryReaderMixin.build_remote_query_options(catalog, "database", options)
        return RemoteQueryReaderMixin.build_remote_query(self._connection_name, query_opts, source_query, "query")

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            TSQLServerDataSource._normalize_quotes(identifier),
            source_start_delimiter=TSQLServerDataSource._IDENTIFIER_DELIMITER["prefix"],
            source_end_delimiter=TSQLServerDataSource._IDENTIFIER_DELIMITER["suffix"],
        )

    @staticmethod
    def _normalize_quotes(identifier: str):
        if DialectUtils.is_already_delimited(identifier, '"', '"'):
            identifier = identifier[1:-1]
            identifier = identifier.replace('""', '"')
            identifier = (
                TSQLServerDataSource._IDENTIFIER_DELIMITER["prefix"]
                + identifier
                + TSQLServerDataSource._IDENTIFIER_DELIMITER["suffix"]
            )
            return identifier

        return identifier
