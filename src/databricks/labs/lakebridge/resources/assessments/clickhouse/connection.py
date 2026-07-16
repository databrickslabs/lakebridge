"""ClickHouse connection wrapper using the clickhouse-connect HTTP client.

Collectors consume ``query()`` results as a list of row dicts. This is intentionally a thin,
collector-facing wrapper distinct from ``connections.database_manager.ClickHouseConnector`` (which
returns ``FetchResult`` for the variant probe / connection test).
"""

from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client as ClickHouseClient


class ClickHouseConnection:
    def __init__(self, config: dict):
        self.config = config
        self.client: ClickHouseClient | None = None

    def connect(self) -> "ClickHouseConnection":
        self.client = clickhouse_connect.get_client(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 8123)),
            username=self.config.get("user", "default"),
            password=self.config.get("password", ""),
            secure=bool(self.config.get("secure", False)),
        )
        return self

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
