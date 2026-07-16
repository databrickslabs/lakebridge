"""Base collector class for all ClickHouse profiling categories.

Ported from the standalone Field Engineering ClickHouse profiler. Each collector runs read-only
queries against ``system.*`` and returns a dict of named result sets. Persistence is owned by the
extract script (``ch_metadata_extract.py``), which flattens each result set into a DuckDB table —
so this base no longer writes per-collector JSON files.
"""

import json
from abc import ABC, abstractmethod
from datetime import datetime, date
from decimal import Decimal
from typing import Any
from uuid import UUID

from databricks.labs.lakebridge.resources.assessments.clickhouse.connection import ClickHouseConnection

# Fields that may contain sensitive data (raw SQL, credentials, network info). When redaction is
# enabled (default ON in the Lakebridge port), these are replaced with "[REDACTED]" in the output.
SENSITIVE_FIELDS = {
    # Raw SQL text — may contain filter values, business logic, PII in predicates
    "query",
    "sample_query",
    "sample_exception",
    # DDL / view definitions — reveals schema design and business logic
    "create_table_query",
    "as_select",
    "view_query",
    # Auth — could contain password hashes, tokens, LDAP credentials
    "auth_params",
    # Network topology
    "host_ip",
    "host_names",
    "host_names_regexp",
    "host_names_like",
    # Row policy filters — WHERE clauses may reference PII columns
    "select_filter",
}

REDACTED = "[REDACTED]"


def redact_value(key: str, value: Any) -> Any:
    """Return ``[REDACTED]`` for a sensitive key, else the value unchanged."""
    return REDACTED if key in SENSITIVE_FIELDS else value


class ProfilerJSONEncoder(json.JSONEncoder):
    """JSON encoder for ClickHouse data types (used for struct/nested columns)."""

    def default(self, o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, bytes):
            return o.decode("utf-8", errors="replace")
        return super().default(o)


class BaseCollector(ABC):
    """Base class for all profiler collectors."""

    name: str = "base"
    description: str = "Base collector"

    def __init__(self, conn: ClickHouseConnection, config: dict):
        self.conn = conn
        self.config = config
        # Coerce to int at the source: days_back is interpolated into `INTERVAL {d} DAY` SQL f-strings
        # across every collector, so a string (or malicious) value from the credentials YAML must never
        # reach a query verbatim. Fall back to 30 on a missing or non-numeric value.
        try:
            self.days_back = int(config.get("days_back", 30))
        except (TypeError, ValueError):
            self.days_back = 30
        self.results: dict[str, Any] = {}
        self.errors: list[str] = []

    @abstractmethod
    def collect(self) -> dict[str, Any]:
        """Run all collection queries and return a dict of named result sets."""

    def safe_query(self, label: str, sql: str) -> list[dict[str, Any]]:
        """Execute a query with error handling. Returns rows or an empty list.

        A missing/unavailable system table (common on OSS builds) degrades to an empty result and a
        recorded warning rather than aborting the collector.
        """
        try:
            rows = self.conn.query(sql)
            print(f"    [{self.name}] {label}: {len(rows)} rows")
            return rows
        except Exception as e:  # non-fatal: record the warning and continue with an empty result
            err_msg = f"[{self.name}] {label}: {str(e)[:200]}"
            self.errors.append(err_msg)
            print(f"    [WARN] {err_msg}")
            return []
