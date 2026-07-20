"""ClickHouse connection wrapper using the clickhouse-connect HTTP client.

Collectors consume ``query()`` results as a list of row dicts. This is intentionally a thin,
collector-facing wrapper distinct from ``connections.database_manager.ClickHouseConnector`` (which
returns ``FetchResult`` for the variant probe / connection test).
"""

from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client as ClickHouseClient

from databricks.labs.lakebridge.resources.assessments.clickhouse import normalize_secure_and_port


class ClickHouseConnection:
    def __init__(self, config: dict):
        self.config = config
        self.client: ClickHouseClient | None = None

    def connect(self) -> "ClickHouseConnection":
        # Host-derived TLS/port so this connection — which carries the password during profiling —
        # is never plaintext-by-default against a Cloud host (and a stray secure: "false" can't
        # downgrade it). Shared with the probe ClickHouseConnector via normalize_secure_and_port.
        secure, port = normalize_secure_and_port(self.config)
        self.client = clickhouse_connect.get_client(
            host=self.config.get("host", "127.0.0.1"),
            port=port,
            username=self.config.get("user", "default"),
            password=self.config.get("password", ""),
            secure=secure,
        )
        return self

    def enable_cluster_reads(self) -> None:
        """Tolerate an unavailable replica in ``clusterAllReplicas()`` reads (Cloud).

        Applied once after Cloud is detected. Harmless for direct (non-cluster) queries, so it is a
        no-op risk on OSS; the profiler only calls it when running against Cloud.
        """
        self._client().set_client_setting("skip_unavailable_shards", 1)

    def _client(self) -> ClickHouseClient:
        if self.client is None:
            self.connect()
        assert self.client is not None
        return self.client

    def query(self, sql: str, parameters: dict | None = None) -> list[dict[str, Any]]:
        """Execute a query and return results as a list of row dicts."""
        result = self._client().query(sql, parameters=parameters)
        columns = result.column_names
        return [dict(zip(columns, row)) for row in result.result_rows]

    def server_version(self) -> str:
        return str(self._client().server_version)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
