"""Base collector class for all ClickHouse profiling categories.

Each collector runs read-only queries against ``system.*`` and returns a dict of named result sets.
Persistence is owned by the extract script (``ch_metadata_extract.py``), which flattens each result
set into a DuckDB table.
"""

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, date
from decimal import Decimal
from typing import Any
from uuid import UUID

from databricks.labs.lakebridge.resources.assessments.clickhouse.connection import ClickHouseConnection

# Fields that may contain sensitive data (raw SQL, credentials, network info); replaced with
# "[REDACTED]" when redaction is enabled (default ON). Matched by exact key name.
SENSITIVE_FIELDS = {
    # Raw SQL text — may contain filter values, business logic, PII in predicates
    "query",
    "sample_query",
    "sample_exception",
    # DDL / view definitions — reveals schema design and business logic
    "create_table_query",
    "as_select",
    "view_query",
    # Column default expressions (system.columns) — may embed literal values / secrets
    "default_expression",
    # Mutation command + failure reason (system.mutations) — raw ALTER/DELETE DDL with the same
    # literal predicates/PII that motivate redacting `query`, and the failure text can echo them back.
    "command",
    "latest_fail_reason",
    # Auth — could contain password hashes, tokens, LDAP credentials
    "auth_params",
    # Network topology — includes the external-source connection string on system.dictionaries.source
    # (host/port/user/db), the same network detail the host_* fields promise to strip.
    "source",
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


def redact_structure(value: Any) -> Any:
    """Redact sensitive keys at any depth of a nested dict/list (struct/map columns), unlike the
    top-level-only ``redact_value``. Applied to query-result rows, not the author-built payloads."""
    if isinstance(value, dict):
        return {key: (REDACTED if key in SENSITIVE_FIELDS else redact_structure(inner)) for key, inner in value.items()}
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    return value


# ClickHouse server error codes for a missing schema object (table/database/column/identifier). These
# are EXPECTED on some OSS builds (e.g. session_log / query_views_log absent) and degrade to an empty
# result; anything else (permissions, syntax, connection) is a real error that must be surfaced.
_MISSING_OBJECT_ERROR_CODES = frozenset({16, 47, 60, 81})  # NO_SUCH_COLUMN, UNKNOWN_IDENTIFIER,
#                                                             UNKNOWN_TABLE, UNKNOWN_DATABASE


def is_missing_object_error(message: str) -> bool:
    """True when an exception message denotes a missing table/column/database (expected on OSS)."""
    text = message.lower()
    code_match = re.search(r"code:\s*(\d+)", text)
    if code_match and int(code_match.group(1)) in _MISSING_OBJECT_ERROR_CODES:
        return True
    # Fall back to phrase matching for clients/builds that don't surface a numeric code.
    return "doesn't exist" in text or "does not exist" in text or "unknown table" in text


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
        # Set once by the extract script from the resolved variant / cloud_mode probe. On ClickHouse
        # Cloud, per-node append-only log tables are local to each replica, so a complete view needs
        # clusterAllReplicas() (see source()); replicated metadata tables must NOT be wrapped.
        self.is_cloud: bool = bool(config.get("is_cloud", False))
        self.results: dict[str, Any] = {}
        self.errors: list[str] = []

    # Per-node append-only log tables: local to each replica on ClickHouse Cloud, so querying them
    # directly returns only the connected replica's rows. clusterAllReplicas() gives the full view.
    # Replicated metadata (tables/columns/parts/users/…) is consistent across replicas and must be
    # queried directly — wrapping it would duplicate rows or error.
    _PER_NODE_LOG_TABLES = frozenset({"query_log", "session_log", "query_views_log", "asynchronous_insert_log"})

    def source(self, table: str) -> str:
        """Return the source-table expression for a ``system.<table>`` reference.

        On Cloud, per-node log tables are wrapped in ``clusterAllReplicas('default', ...)`` so all
        replicas are included; everything else (and all of OSS) is queried directly. The
        ``skip_unavailable_shards`` setting these queries rely on is applied once as a session setting
        on the Cloud connection (see ``ClickHouseConnection``), so no per-query SETTINGS clause is needed.
        """
        if self.is_cloud and table in self._PER_NODE_LOG_TABLES:
            return f"clusterAllReplicas('default', system.{table})"
        return f"system.{table}"

    @abstractmethod
    def collect(self) -> dict[str, Any]:
        """Run all collection queries and return a dict of named result sets."""

    def safe_query(self, label: str, sql: str) -> list[dict[str, Any]]:
        """Execute a query, returning rows or an empty list.

        A *missing* system table/column (common on OSS builds) degrades to an empty result and a
        recorded warning. A *real* error (permission denied, bad SQL, schema drift, connection loss)
        is recorded as an error-level entry — so it surfaces in the run's warnings instead of being
        masked as a successful empty extract — but the collector still continues so one failing query
        doesn't abort the whole run.
        """
        try:
            rows = self.conn.query(sql)
            print(f"    [{self.name}] {label}: {len(rows)} rows")
            return rows
        except Exception as e:  # non-fatal: classify below, record, and continue with an empty result
            detail = str(e)[:200]
            if is_missing_object_error(str(e)):
                err_msg = f"[{self.name}] {label}: {detail}"
                self.errors.append(err_msg)
                print(f"    [WARN] {err_msg}")
            else:
                # Real failure — mark it distinctly so it is visible in the payload's warnings.
                err_msg = f"[ERROR][{self.name}] {label}: {detail}"
                self.errors.append(err_msg)
                print(f"    [ERROR] {err_msg}")
            return []
