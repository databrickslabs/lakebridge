import re
import logging
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
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class OracleDataSource(DataSource, SecretsMixin, JDBCReaderMixin):
    _DRIVER = "oracle"
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
        secret_scope: str,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._secret_scope = secret_scope

    @property
    def get_jdbc_url(self) -> str:
        return (
            f"jdbc:{OracleDataSource._DRIVER}:thin:{self._get_secret('user')}"
            f"/{self._get_secret('password')}@//{self._get_secret('host')}"
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
        table_query = query.replace(":tbl", f"{schema}.{table}")
        try:
            if options is None:
                return self.reader(table_query).options(**self._get_timestamp_options()).load()
            reader_options = self._get_jdbc_reader_options(options) | self._get_timestamp_options()
            df = self.reader(table_query).options(**reader_options).load()
            logger.warning(f"Fetching data using query: \n`{table_query}`")

            # Convert all column names to lower case
            df = df.select([col(c).alias(c.lower()) for c in df.columns])
            return df
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
            OracleDataSource._SCHEMA_QUERY.format(table=table, owner=schema),
        )
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self.reader(schema_query).load()
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            logger.debug(f"schema_metadata: ${schema_metadata}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    @staticmethod
    def _get_timestamp_options() -> dict[str, str]:
        return {
            "oracle.jdbc.mapDateToTimestamp": "False",
            "sessionInitStatement": "BEGIN dbms_session.set_nls('nls_date_format', "
            "'''YYYY-MM-DD''');dbms_session.set_nls('nls_timestamp_format', '''YYYY-MM-DD "
            "HH24:MI:SS''');END;",
        }

    def reader(self, query: str) -> DataFrameReader:
        return self._get_jdbc_reader(query, self.get_jdbc_url, OracleDataSource._DRIVER)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        normalized = DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=OracleDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=OracleDataSource._IDENTIFIER_DELIMITER,
        )

        # TODO: In Oracle, quoted identifiers are case-sensitive,
        # it is disabled for now till we have a proper strategy to handle it.
        normalized.source_normalized = DialectUtils.unnormalize_identifier(identifier)

        return normalized
