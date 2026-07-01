import logging
import re
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


class BigQueryDataSource(DataSource):
    """BigQuery tables are referenced as ``project.dataset.table`` (``catalog`` is the project).
    Results materialize into the ``lakebridge_reconcile`` dataset, which must exist and be writable
    by the connection's service account.

    Schema type handling
    ---------------------
    * ``NUMERIC``/``BIGNUMERIC`` -> ``decimal(38, 9)`` (BigQuery's NUMERIC default; BIGNUMERIC is
      truncated to fit Databricks' max precision of 38 — a lossy but same-family mapping).
    * ``JSON`` -> ``variant``.

    Columns with no same-family equivalent (``TIME``, ``RANGE<T>``, ``INTERVAL`` or these nested in
    ``ARRAY``/``STRUCT``) can be reported as schema mismatches even when the migration is correct — an
    accepted false negative, logged in ``get_schema`` so the user knows to verify them manually.
    """

    _IDENTIFIER_DELIMITER = "`"

    _APPROXIMATE_TYPES = ("time", "bignumeric", "range", "interval")

    _LIST_SCHEMAS_QUERY = "select schema_name from `{catalog}`.INFORMATION_SCHEMA.SCHEMATA order by schema_name"
    _LIST_TABLES_QUERY = "select table_name from `{catalog}.{schema}`.INFORMATION_SCHEMA.TABLES order by table_name"
    _SCHEMA_QUERY = """select column_name,
                                  case
                                        when data_type = 'NUMERIC' then 'decimal(38, 9)'
                                        when data_type like 'BIGNUMERIC%' then 'decimal(38, 9)'
                                        when data_type = 'JSON' then 'variant'
                                        else data_type
                                  end as data_type
                                  from `{catalog}.{schema}`.INFORMATION_SCHEMA.COLUMNS
                                  where table_name = '{table}'
                                  order by ordinal_position"""

    _MATERIALIZATION_DATASET = "lakebridge_reconcile"

    def __init__(self, engine: Dialect, reader: RemoteQueryReader):
        self._engine = engine
        self._reader = reader

    def _read(self, query: str) -> DataFrame:
        return self._reader.read_data(query, self._MATERIALIZATION_DATASET, "materializationDataset")

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        table_ref = f"`{catalog}.{schema}.{table}`"
        table_query = query.replace(":tbl", table_ref).replace("@tbl", table_ref)
        try:
            logger.info(f"Fetching data using query: \n`{table_query}`")
            df = self._read(table_query)
            return df.select([col(column).alias(column.lower()) for column in df.columns])
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
            BigQueryDataSource._SCHEMA_QUERY.format(catalog=catalog, schema=schema, table=table),
        )
        logger.debug(f"Fetching schema using query: \n`{schema_query}`")
        logger.info(f"Fetching Schema: Started at: {datetime.now()}")
        try:
            df = self._read(schema_query)
            schema_metadata = df.select([col(c).alias(c.lower()) for c in df.columns]).collect()
            logger.info(f"Schema fetched successfully. Completed at: {datetime.now()}")
            schemas = [self._map_meta_column(field, normalize) for field in schema_metadata]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schema", schema_query)

        self._warn_on_approximate_types(schemas)
        return schemas

    def _warn_on_approximate_types(self, schema: list[Schema]) -> None:
        """Log columns whose BigQuery type has no exact Databricks equivalent."""
        pattern = rf"\b({'|'.join(BigQueryDataSource._APPROXIMATE_TYPES)})\b"
        approximate = [s.column_name for s in schema if re.search(pattern, s.data_type.lower())]
        if approximate:
            logger.warning(
                f"BigQuery columns {approximate} use a type with no exact Databricks equivalent (e.g. TIME, "
                "BIGNUMERIC, RANGE, INTERVAL, or these nested in ARRAY/STRUCT); schema reconciliation may report "
                "them as mismatches even when the data migrated correctly. Verify these columns manually."
            )

    def list_schemas(self, catalog: str) -> list[str]:
        query = BigQueryDataSource._LIST_SCHEMAS_QUERY.format(catalog=catalog)
        try:
            df = self._read(query)
            return [row.schema_name for row in df.select(col("schema_name").alias("schema_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "schemas", query)

    def list_tables(self, catalog: str, schema: str) -> list[str]:
        query = BigQueryDataSource._LIST_TABLES_QUERY.format(catalog=catalog, schema=schema)
        try:
            df = self._read(query)
            return [row.table_name for row in df.select(col("table_name").alias("table_name")).collect()]
        except (RuntimeError, PySparkException) as e:
            return self.log_and_throw_exception(e, "tables", query)

    def normalize_identifier(self, identifier: str) -> NormalizedIdentifier:
        return DialectUtils.normalize_identifier(
            identifier,
            source_start_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
            source_end_delimiter=BigQueryDataSource._IDENTIFIER_DELIMITER,
        )
