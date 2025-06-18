import logging
import re
from datetime import datetime

from pyspark.errors import PySparkException
from pyspark.sql import DataFrame, DataFrameReader, SparkSession
from pyspark.sql.functions import col
from sqlglot import Dialect
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.jdbc_reader import JDBCReaderMixin
from databricks.labs.lakebridge.reconcile.connectors.secrets import SecretsMixin
from databricks.labs.lakebridge.reconcile.exception import InvalidSnowflakePemPrivateKey
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions, Schema
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

logger = logging.getLogger(__name__)


class SnowflakeDataSource(DataSource, SecretsMixin, JDBCReaderMixin):
    _DRIVER = "snowflake"
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
        secret_scope: str,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._secret_scope = secret_scope

    @property
    def get_jdbc_url(self) -> str:
        try:
            sf_password = self._get_secret('sfPassword')
        except (NotFound, KeyError) as e:
            message = "sfPassword is mandatory for jdbc connectivity with Snowflake."
            logger.error(message)
            raise NotFound(message) from e

        return (
            f"jdbc:{SnowflakeDataSource._DRIVER}://{self._get_secret('sfAccount')}.snowflakecomputing.com"
            f"/?user={self._get_secret('sfUser')}&password={sf_password}"
            f"&db={self._get_secret('sfDatabase')}&schema={self._get_secret('sfSchema')}"
            f"&warehouse={self._get_secret('sfWarehouse')}&role={self._get_secret('sfRole')}"
        )

    @staticmethod
    def get_private_key(pem_private_key: str) -> str:
        try:
            private_key_bytes = pem_private_key.encode("UTF-8")
            p_key = serialization.load_pem_private_key(
                private_key_bytes,
                password=None,
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
            schema_metadata = self.reader(schema_query).load().collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [Schema(field.COLUMN_NAME.lower(), field.DATA_TYPE.lower()) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def reader(self, query: str) -> DataFrameReader:
        options = {
            "sfUrl": self._get_secret('sfUrl'),
            "sfUser": self._get_secret('sfUser'),
            "sfDatabase": self._get_secret('sfDatabase'),
            "sfSchema": self._get_secret('sfSchema'),
            "sfWarehouse": self._get_secret('sfWarehouse'),
            "sfRole": self._get_secret('sfRole'),
        }
        try:
            options["pem_private_key"] = SnowflakeDataSource.get_private_key(self._get_secret('pem_private_key'))
        except (NotFound, KeyError):
            logger.warning("pem_private_key not found. Checking for sfPassword")
            try:
                options["sfPassword"] = self._get_secret('sfPassword')
            except (NotFound, KeyError) as e:
                message = "sfPassword and pem_private_key not found. Either one is required for snowflake auth."
                logger.error(message)
                raise NotFound(message) from e

        return self._spark.read.format("snowflake").option("dbtable", f"({query}) as tmp").options(**options)
