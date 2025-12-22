import logging
import re
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, DataFrameReader, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from databricks.labs.lakebridge.reconcile.connectors.credentials import (
    load_and_validate_credentials,
    ReconcileCredentialsConfig,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.jdbc_reader import JDBCReaderMixin
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils, NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.exception import InvalidSnowflakePemPrivateKey
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class SnowflakeDataSource(DataSource, JDBCReaderMixin):
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

    def __init__(self, engine: Dialect, spark: SparkSession, ws: WorkspaceClient):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._creds_or_empty: dict[str, str] = {}

    @property
    def _creds(self):
        if self._creds_or_empty:
            return self._creds_or_empty
        raise RuntimeError("Snowflake credentials have not been loaded. Please call load_credentials() first.")

    def load_credentials(self, creds: ReconcileCredentialsConfig) -> "SnowflakeDataSource":
        password = creds.vault_secret_names.get("sfPassword")
        pem_key = creds.vault_secret_names.get("pem_private_key")
        if password and pem_key:  # user did not specify auth method after migrating from secret scope
            logger.warning(
                f"Snowflake auth not specified after migrating from secret scope so defaulting to sfPassword. "
                f"Please update the creds config and include the necessary keys. Docs: {self._DOCS_URL}."
            )
            creds.vault_secret_names.pop("pem_private_key")

        self._creds_or_empty = load_and_validate_credentials(creds, self._ws, "snowflake")

        # Ensure at least one authentication method is provided
        assert any(
            self._creds.get(k) for k in ("sfPassword", "pem_private_key")
        ), "Missing Snowflake credentials. Please configure any of [sfPassword, pem_private_key]."

        # Process PEM private key if provided
        if self._creds.get("pem_private_key"):
            self._creds["pem_private_key"] = SnowflakeDataSource._get_private_key(
                self._creds["pem_private_key"],
                self._creds.get("pem_private_key_password"),
            )

        return self

    @property
    def get_jdbc_url(self) -> str:
        if not self._creds:
            raise RuntimeError("Credentials not loaded. Please call `load_credentials(ReconcileCredentialsConfig)`.")

        return (
            f"jdbc:{SnowflakeDataSource._DRIVER}://{self._creds['sfUrl']}"
            f"/?user={self._creds['sfUser']}&password={self._creds['sfPassword']}"
            f"&db={self._creds['sfDatabase']}&schema={self._creds['sfSchema']}"
            f"&warehouse={self._creds['sfWarehouse']}&role={self._creds['sfRole']}"
        )  # TODO Support PEM key auth

    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        table_query = query.replace(":tbl", f"{catalog}.{schema}.{table}")
        try:
            if options is None:
                df = self.reader(table_query).load()
            else:
                options = self._get_jdbc_reader_options(options)
                df = (
                    self._get_jdbc_reader(table_query, self.get_jdbc_url, SnowflakeDataSource._DRIVER)
                    .options(**options)
                    .load()
                )
            return df.select([col(column).alias(column.lower()) for column in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", table_query)

    def get_schema(
        self,
        catalog: str | None,
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
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self.reader(schema_query).load()
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def reader(self, query: str) -> DataFrameReader:
        if not self._creds:
            raise RuntimeError("Credentials not loaded. Please call `load_credentials(ReconcileCredentialsConfig)`.")

        return self._spark.read.format("snowflake").option("dbtable", f"({query}) as tmp").options(**self._creds)

    @staticmethod
    def _get_private_key(pem_private_key: str, pem_private_key_password: str | None) -> str:
        try:
            private_key_bytes = pem_private_key.encode("UTF-8")
            password_bytes = pem_private_key_password.encode("UTF-8") if pem_private_key_password else None
        except UnicodeEncodeError as e:
            message = f"Invalid pem key and/or pem password: unable to encode. --> {e}"
            logger.error(message)
            raise ValueError(message) from e

        try:
            p_key = serialization.load_pem_private_key(
                private_key_bytes,
                password_bytes,
                backend=default_backend(),
            )
            pkb = p_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pkb_str = pkb.decode("UTF-8")
            # Remove the first and last lines (BEGIN/END markers)
            private_key_pem_lines = pkb_str.strip().split('\n')[1:-1]
            # Join the lines to form the base64 encoded string
            private_key_pem_str = ''.join(private_key_pem_lines)
            return private_key_pem_str
        except Exception as e:
            message = f"Failed to load or process the provided PEM private key. --> {e}"
            logger.error(message)
            raise InvalidSnowflakePemPrivateKey(message) from e

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
