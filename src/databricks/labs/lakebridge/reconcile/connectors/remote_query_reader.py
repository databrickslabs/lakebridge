from dataclasses import asdict
from databricks.labs.lakebridge.reconcile.recon_config import JdbcReaderOptions


class RemoteQueryReaderMixin:

    @staticmethod
    def build_remote_query(
        connection_name: str, query_options: str, source_query: str, source_query_key: str = "query"
    ) -> str:
        escaped = source_query.replace("'", "\\'")
        sql = f"SELECT * FROM remote_query('{connection_name}', {source_query_key} => '{escaped}', {query_options})"
        return sql

    @staticmethod
    def build_remote_query_options(
        catalog: str, catalog_key: str = "database", options: JdbcReaderOptions | None = None
    ) -> str:
        def camelcase(underscored):
            parts = underscored.split('_')
            return parts[0] + ''.join(word.capitalize() for word in parts[1:])

        def encode(key, value):
            return f"{camelcase(key)} => '{value}'"

        opts = {catalog_key: catalog, **(asdict(options) if options else {})}

        encoded = [encode(k, v) for k, v in opts.items()]
        return ", ".join(encoded)
