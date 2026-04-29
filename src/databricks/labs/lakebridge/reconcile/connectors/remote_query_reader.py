from dataclasses import asdict

from pyspark.sql import DataFrame, SparkSession

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


class RemoteQueryReader:

    def __init__(self, spark: SparkSession, connection_name: str):
        self._spark = spark
        self._connection_name = connection_name

    def read_data(
        self,
        source_query: str,
        catalog: str,
        catalog_key: str = "database",
        source_query_key: str = "query",
        options: JdbcReaderOptions | None = None,
    ) -> DataFrame:
        query_options = self._build_options(catalog, catalog_key, options)
        query = self._build_query(query_options, source_query, source_query_key)
        return self._spark.sql(query)

    def _build_query(self, query_options: str, source_query: str, source_query_key: str) -> str:
        escaped = source_query.replace("'", r"\'")
        return (
            f"SELECT * FROM remote_query('{self._connection_name}', {source_query_key} => '{escaped}', {query_options})"
        )

    @staticmethod
    def _build_options(catalog: str, catalog_key: str, options: JdbcReaderOptions | None = None) -> str:
        def camelcase(underscored):
            parts = underscored.split('_')
            return parts[0] + ''.join(word.capitalize() for word in parts[1:])

        def encode(key, value):
            if key == "partition_column":
                value = DialectUtils.unnormalize_identifier(value)  # revert to original value without backticks
            return f"{camelcase(key)} => '{value}'"

        opts = {catalog_key: catalog, **(asdict(options) if options else {})}
        encoded = [encode(k, v) for k, v in opts.items()]

        return ", ".join(encoded)
