import re
import logging
from collections.abc import Mapping
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, DataFrameReader, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.jdbc_reader import JDBCReaderMixin
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.connectors.secrets import SecretsMixin
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema, OptionalPrimitiveType
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

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


class RedshiftDataSource(DataSource, SecretsMixin, JDBCReaderMixin):
    _DRIVER = "redshift"
    _IDENTIFIER_DELIMITER = "\""

    def __init__(
        self,
        engine: Dialect,
        spark: SparkSession,
        ws: WorkspaceClient,
        secret_scope: str,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._secret_scope = secret_scope

    @property
    def get_jdbc_url(self) -> str:
        return (
            f"jdbc:{RedshiftDataSource._DRIVER}://{self._get_secret('host')}"
            f":{self._get_secret('port')}/{self._get_secret('database')}"
        )

    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        # Redshift dialect in SQLGlot converts :tbl to %(tbl)s (PostgreSQL parameter syntax)
        table_query = query.replace("%(tbl)s", f"{schema}.{table}").replace(":tbl", f"{schema}.{table}")
        try:
            if options is None:
                df = self.reader(table_query).load()
            else:
                reader_options = self._get_jdbc_reader_options(options)
                df = self.reader(table_query, reader_options).load()
            return df.select([col(c).alias(c.lower()) for c in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", table_query)

    def get_schema(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        schema_query = re.sub(
            r'\s+',
            ' ',
            _SCHEMA_QUERY.format(schema=schema, table=table),
        )
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self.reader(schema_query).load()
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def reader(self, query: str, options: Mapping[str, OptionalPrimitiveType] | None = None) -> DataFrameReader:
        if options is None:
            options = {}
        user = self._get_secret('user')
        password = self._get_secret('password')
        return self._get_jdbc_reader(
            query, self.get_jdbc_url, RedshiftDataSource._DRIVER, {**options, "user": user, "password": password}
        )

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=RedshiftDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=RedshiftDataSource._IDENTIFIER_DELIMITER,
        )
