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


class TSQLServerDataSource(DataSource, JDBCReaderMixin):
    _DRIVER = "sqlserver"
    _IDENTIFIER_DELIMITER = {"prefix": "[", "suffix": "]"}

    def __init__(
        self,
        engine: Dialect,
        spark: SparkSession,
        ws: WorkspaceClient,
    ):
        self._engine = engine
        self._spark = spark
        self._ws = ws
        self._creds_or_empty: dict[str, str] = {}

    @property
    def _creds(self):
        if self._creds_or_empty:
            return self._creds_or_empty
        raise RuntimeError("MS SQL/Synapse credentials have not been loaded. Please call load_credentials() first.")

    @property
    def get_jdbc_url(self) -> str:
        # Construct the JDBC URL
        return (
            f"jdbc:{self._DRIVER}://{self._creds.get('host')}:{self._creds.get('port')};"
            f"databaseName={self._creds.get('database')};"
            f"user={self._creds.get('user')};"
            f"password={self._creds.get('password')};"
            f"encrypt={self._creds.get('encrypt')};"
            f"trustServerCertificate={self._creds.get('trustServerCertificate')};"
        )

    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        table_query = query.replace(":tbl", f"{catalog}.{schema}.{self.normalize_identifier(table).source_normalized}")
        with_clause_pattern = re.compile(r'WITH\s+.*?\)\s*(?=SELECT)', re.IGNORECASE | re.DOTALL)
        match = with_clause_pattern.search(table_query)
        if match:
            prepare_query_string = match.group(0)
            query = table_query.replace(match.group(0), '')
        else:
            query = table_query
            prepare_query_string = ""
        try:
            if options is None:
                df = self.reader(query, prepare_query_string).load()
            else:
                options = self._get_jdbc_reader_options(options)
                df = self._get_jdbc_reader(table_query, self.get_jdbc_url, self._DRIVER).options(**options).load()
            return df.select([col(column).alias(column.lower()) for column in df.columns])
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "data", table_query)

    def load_credentials(self, creds: ReconcileCredentialConfig) -> "TSQLServerDataSource":
        connector_creds = [
            "host",
            "port",
            "database",
            "user",
            "password",
            "encrypt",
            "trustServerCertificate",
        ]

        use_scope = creds.source_creds.get("__secret_scope")
        if use_scope:
            logger.warning(
                f"Secret scope configuration is deprecated. Please refer to the docs {self._DOCS_URL} to update."
            )
            source_creds = {key: f"{use_scope}/{key}" for key in connector_creds}

            assert creds.vault_type == "databricks", "Secret scope provided, vault_type must be 'databricks'"
            parsed_creds = build_credentials(creds.vault_type, "mssql", source_creds)
        else:
            parsed_creds = build_credentials(creds.vault_type, "mssql", creds.source_creds)

        self._creds_or_empty = create_credential_manager(parsed_creds, self._ws).get_credentials("mssql")
        assert all(
            self._creds.get(k) for k in connector_creds
        ), f"Missing mandatory MS SQL credentials. Please configure all of {connector_creds}."

        return self

    def get_schema(
        self,
        catalog: str | None,
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
        try:
            logger.debug(f"Fetching schema using query: \n`{schema_query}`")
            logger.info(f"Fetching Schema: Started at: {datetime.now()}")
            df = self.reader(schema_query).load()
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            return [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

    def reader(self, query: str, prepare_query_str="") -> DataFrameReader:
        return self._get_jdbc_reader(query, self.get_jdbc_url, self._DRIVER, {"prepareQuery": prepare_query_str})

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
