import re
import logging
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, DataFrameReader, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect

from databricks.labs.lakebridge.config import ReconcileCredentialConfig
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager, build_credentials
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.jdbc_reader import JDBCReaderMixin
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils, NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class OracleDataSource(DataSource, JDBCReaderMixin):
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

    def __init__(self, engine: Dialect, spark: SparkSession, ws: WorkspaceClient):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._creds_or_empty: dict[str, str] = {}

    @property
    def _creds(self):
        if self._creds_or_empty:
            return self._creds_or_empty
        raise RuntimeError("Oracle credentials have not been loaded. Please call load_credentials() first.")

    @property
    def get_jdbc_url(self) -> str:
        return (
            f"jdbc:{OracleDataSource._DRIVER}:thin:@//{self._creds.get('host')}"
            f":{self._creds.get('port')}/{self._creds.get('database')}"
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
            OracleDataSource._SCHEMA_QUERY.format(table=table.lower(), owner=schema.lower()),
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
            "oracle.jdbc.mapDateToTimestamp": "false",
            "sessionInitStatement": "BEGIN dbms_session.set_nls('nls_date_format', "
            "'''YYYY-MM-DD''');dbms_session.set_nls('nls_timestamp_format', '''YYYY-MM-DD "
            "HH24:MI:SS''');END;",
        }

    def reader(self, query: str) -> DataFrameReader:
        user = self._creds.get('user')
        password = self._creds.get('password')
        logger.debug(f"Using user: {user} to connect to Oracle")
        return self._get_jdbc_reader(
            query, self.get_jdbc_url, OracleDataSource._DRIVER, {"user": user, "password": password}
        )

    def load_credentials(self, creds: ReconcileCredentialConfig) -> "OracleDataSource":
        connector_creds = [
            "host",
            "port",
            "database",
            "user",
            "password",
        ]

        use_scope = creds.source_creds.get("__secret_scope")
        if use_scope:
            source_creds = {key: f"{use_scope}/{key}" for key in connector_creds}

            assert creds.vault_type == "databricks", "Secret scope provided, vault_type must be 'databricks'"
            parsed_creds = build_credentials(creds.vault_type, "oracle", source_creds)
        else:
            parsed_creds = build_credentials(creds.vault_type, "oracle", creds.source_creds)

        self._creds_or_empty = create_credential_manager(parsed_creds, self._ws).get_credentials("oracle")
        assert all(
            self._creds.get(k) for k in connector_creds
        ), f"Missing mandatory Oracle credentials. Please configure all of {connector_creds}."

        return self

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
