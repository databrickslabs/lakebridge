import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

from databricks.labs.lakebridge.resources.assessments.clickhouse import ch_metadata_extract
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.base import (
    SENSITIVE_FIELDS,
    redact_value,
)
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.workload import WorkloadCollector
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.security import SecurityCollector


class _FakeConnection:
    """Stand-in for ClickHouseConnection: returns canned rows and records the SQL it saw.

    Every query returns a single row carrying a sensitive field (``query``) and a benign field so
    tests can assert both table creation and redaction. The ``cloud_mode`` probe is answered from
    ``cloud_mode`` so the costs collector's OSS/Cloud branch can be exercised.
    """

    def __init__(self, config: dict):
        self.config = config
        self.cloud_mode = config.get("_test_cloud_mode", "0")

    def connect(self):
        return self

    def server_version(self) -> str:
        return "24.1.1.1"

    def query(self, sql: str, _parameters=None):
        if "cloud_mode" in sql:
            return [{"value": self.cloud_mode}]
        # Mimic an OSS-empty / absent system table (e.g. session_log): zero rows.
        if "system.session_log" in sql:
            return []
        # Carry several sensitive fields + a benign metric so both redaction and preservation can be
        # asserted end-to-end through the real DuckDB write path.
        return [
            {
                "query": "SELECT secret FROM t",
                "command": "ALTER TABLE t DELETE WHERE ssn = '123'",
                "source": "clickhouse://user:pw@10.0.0.1:9000/db",
                "runs": 5,
                "compute_weight": 100,
            }
        ]

    def enable_cluster_reads(self) -> None:
        pass

    def close(self):
        pass


def _run(monkeypatch, tmp_path, credentials) -> Path:
    db_path = tmp_path / "clickhouse_extract.db"
    monkeypatch.setattr(ch_metadata_extract, "ClickHouseConnection", _FakeConnection)
    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = credentials
    ch_metadata_extract.execute(credential_manager=cred_manager, db_path=str(db_path))
    return db_path


def _tables(db_path: Path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}


@pytest.fixture
def oss_credentials():
    return {"host": "127.0.0.1", "port": 8123, "secure": False, "profiler": {"days_back": 7, "redact": True}}


def test_extract_writes_one_table_per_result_set(monkeypatch, tmp_path, oss_credentials, capsys):
    db_path = _run(monkeypatch, tmp_path, oss_credentials)
    tables = _tables(db_path)

    # A representative table from each collector (one DuckDB table per named result set).
    for expected in (
        "workload_query_volume_summary",
        "objects_databases",
        "features_engine_usage",
        "dependencies_object_dependencies",
        "utilization_current_metrics",
        "security_users",
        "costs_storage_by_table",
    ):
        assert expected in tables, f"missing table {expected}; got {sorted(tables)}"

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "success"
    assert payload["tables"]


def test_extract_redacts_sensitive_fields_by_default(monkeypatch, tmp_path, oss_credentials):
    db_path = _run(monkeypatch, tmp_path, oss_credentials)
    with duckdb.connect(str(db_path)) as conn:
        # The `query` column is sensitive -> must be redacted with redact defaulting on.
        value = conn.execute("SELECT query FROM workload_slowest_queries LIMIT 1").fetchone()[0]
        # Regression for the review finding: mutations.command and dictionaries.source are also
        # sensitive and must be redacted end-to-end (not just query).
        command = conn.execute("SELECT command FROM utilization_mutations LIMIT 1").fetchone()[0]
        source = conn.execute("SELECT source FROM objects_dictionaries LIMIT 1").fetchone()[0]
    assert value == "[REDACTED]"
    assert command == "[REDACTED]"
    assert source == "[REDACTED]"


def test_extract_keeps_sensitive_fields_when_redaction_disabled(monkeypatch, tmp_path):
    creds = {"host": "127.0.0.1", "port": 8123, "profiler": {"days_back": 7, "redact": False}}
    db_path = _run(monkeypatch, tmp_path, creds)
    with duckdb.connect(str(db_path)) as conn:
        value = conn.execute("SELECT query FROM workload_slowest_queries LIMIT 1").fetchone()[0]
    assert value == "SELECT secret FROM t"


def test_empty_result_set_creates_typed_table_from_catalog(monkeypatch, tmp_path, oss_credentials, capsys):
    """An empty result set (0 rows, e.g. session_log empty on OSS) must still create the table from the
    declared schema catalog so every table always exists (consistent with the mssql/BigQuery
    profilers), and must not abort the extract (regression: DuckDB rejects a 0-column table)."""
    db_path = _run(monkeypatch, tmp_path, oss_credentials)
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["status"] == "success"
    # session_activity is empty -> 0 rows, but the table exists with its catalog-declared columns.
    assert payload["rows"]["security_session_activity"] == 0
    assert "security_session_activity" in _tables(db_path)
    with duckdb.connect(str(db_path)) as conn:
        cols = {c[0] for c in conn.execute("DESCRIBE security_session_activity").fetchall()}
        row_count = conn.execute("SELECT count(*) FROM security_session_activity").fetchone()[0]
    assert row_count == 0
    assert {"type", "user", "auth_type", "event_count"} <= cols


def test_table_schema_catalog_covers_every_result_set():
    """The schema catalog must declare an entry for every <collector>_<result_set> a collector can
    emit, so an empty result set always gets a typed stub table. Guards against a new collector query
    landing without a catalog entry (which would silently skip the table)."""
    package_dir = Path(ch_metadata_extract.__file__).parent
    collectors_dir = package_dir / "collectors"
    expected: set[str] = set()
    for collector_file in collectors_dir.glob("*.py"):
        if collector_file.stem in {"__init__", "base"}:
            continue
        text = collector_file.read_text(encoding="utf-8")
        name_match = re.search(r'name\s*=\s*["\']([a-z_]+)["\']', text)
        collector_name = name_match.group(1) if name_match else collector_file.stem
        for key in re.findall(r'results\[["\']([a-z_]+)["\']\]', text):
            expected.add(f"{collector_name}_{key}")

    catalog = json.loads((package_dir / "table_schemas.json").read_text(encoding="utf-8"))
    missing = expected - set(catalog)
    assert not missing, f"table_schemas.json missing entries: {sorted(missing)}"


@pytest.mark.parametrize(
    ("table", "enrichment_columns"),
    [
        # Columns added in Python AFTER the query (not returned by ClickHouse). A populated run has
        # them; an empty run gets them only if the catalog declares them. If they drift, the empty
        # stub and the populated table would differ in shape — this pins the catalog to include them.
        ("costs_storage_by_table", {"compressed_gb", "disk_gb"}),
        ("costs_compute_by_user", {"compute_weight_pct"}),
        ("costs_compute_by_database", {"compute_weight_pct"}),
    ],
)
def test_schema_catalog_declares_python_enrichment_columns(table, enrichment_columns):
    """The empty-stub schema for an enriched table must include the columns added post-query, so an
    empty run and a populated run produce the same table shape."""
    package_dir = Path(ch_metadata_extract.__file__).parent
    catalog = json.loads((package_dir / "table_schemas.json").read_text(encoding="utf-8"))
    declared = {col for col, _ in catalog.get(table, [])}
    assert enrichment_columns <= declared, f"{table} catalog missing enrichment cols {enrichment_columns - declared}"


def test_costs_oss_has_no_dollar_tables(monkeypatch, tmp_path, oss_credentials):
    """OSS: costs reports a resource footprint and no actual-billed-cost (no Cloud API)."""
    db_path = _run(monkeypatch, tmp_path, oss_credentials)
    with duckdb.connect(str(db_path)) as conn:
        pricing_cols = {c[0] for c in conn.execute("DESCRIBE costs_pricing_config").fetchall()}
        footprint = {c[0] for c in conn.execute("DESCRIBE costs_resource_footprint").fetchall()}
    assert "actual_billed_cost" not in pricing_cols
    assert "total_disk_gb" in footprint


def test_rerun_into_same_db_resets_variant_shaped_tables(monkeypatch, tmp_path):
    """Re-running into the same output DuckDB must reset tables that change shape by variant.
    A Cloud run writes an 8-column costs_pricing_config; a subsequent OSS run into the same file must
    replace it with the 4-column OSS shape (regression: previously TRUNCATE-into-stale-schema errored)."""
    db_path = tmp_path / "clickhouse_extract.db"
    monkeypatch.setattr(ch_metadata_extract, "ClickHouseConnection", _FakeConnection)
    cred_manager = MagicMock()

    # Run 1: Cloud-shaped (host suffix -> is_cloud True -> pricing_config carries region/tier metadata).
    cred_manager.get_credentials.return_value = {
        "host": "svc.us-east-1.aws.clickhouse.cloud",
        "profiler": {"days_back": 7, "redact": True},
    }
    ch_metadata_extract.execute(credential_manager=cred_manager, db_path=str(db_path))
    with duckdb.connect(str(db_path)) as conn:
        cloud_cols = {c[0] for c in conn.execute("DESCRIBE costs_pricing_config").fetchall()}
    assert "region_detected" in cloud_cols  # Cloud-only column
    assert "deployment" not in cloud_cols

    # Run 2: OSS-shaped into the SAME db -> must succeed and carry the OSS shape, not the stale Cloud one.
    cred_manager.get_credentials.return_value = {
        "host": "127.0.0.1",
        "profiler": {"days_back": 7, "redact": True},
    }
    ch_metadata_extract.execute(credential_manager=cred_manager, db_path=str(db_path))
    with duckdb.connect(str(db_path)) as conn:
        oss_cols = {c[0] for c in conn.execute("DESCRIBE costs_pricing_config").fetchall()}
        is_cloud = conn.execute("SELECT is_cloud FROM costs_pricing_config").fetchone()[0]
    assert "region_detected" not in oss_cols  # Cloud-only column must be gone after reset
    assert "deployment" in oss_cols  # OSS-only column
    assert is_cloud is False


@pytest.mark.parametrize(
    ("raw_days_back", "expected"),
    [
        (7, 7),
        ("14", 14),
        ("30 DAY) OR 1=1 --", 30),  # injection attempt -> falls back to safe default
        (None, 30),
        ("garbage", 30),
    ],
)
def test_base_collector_coerces_days_back_to_int(raw_days_back, expected):
    """days_back is interpolated into SQL f-strings, so it must be a safe int regardless of the
    stored value (string, injection attempt, or garbage) -> falls back to 30 when not numeric."""
    collector = WorkloadCollector(conn=MagicMock(), config={"days_back": raw_days_back})
    assert collector.days_back == expected
    assert isinstance(collector.days_back, int)


def test_source_wraps_per_node_logs_on_cloud_only():
    """On Cloud, per-node log tables are wrapped in clusterAllReplicas so all replicas are read;
    replicated metadata tables are never wrapped, and OSS wraps nothing (single node)."""
    cloud = WorkloadCollector(conn=MagicMock(), config={"is_cloud": True})
    oss = WorkloadCollector(conn=MagicMock(), config={"is_cloud": False})

    # Per-node append-only logs -> wrapped on Cloud, direct on OSS.
    for tbl in ("query_log", "session_log", "query_views_log", "asynchronous_insert_log"):
        assert cloud.source(tbl) == f"clusterAllReplicas('default', system.{tbl})"
        assert oss.source(tbl) == f"system.{tbl}"

    # Replicated / consistent metadata -> never wrapped, even on Cloud (would duplicate/error).
    for tbl in ("tables", "columns", "parts", "users", "grants"):
        assert cloud.source(tbl) == f"system.{tbl}"
        assert oss.source(tbl) == f"system.{tbl}"


def test_cloud_collectors_read_query_log_across_replicas():
    """Regression for the review finding: non-cost collectors must use clusterAllReplicas for
    query_log/session_log on Cloud (otherwise they see only the connected replica)."""
    for cls in (WorkloadCollector, SecurityCollector):
        seen: list[str] = []
        collector = cls(conn=MagicMock(), config={"days_back": 7, "is_cloud": True})
        collector.conn.query = lambda sql, sink=seen: sink.append(sql) or []
        collector.collect()
        joined = "\n".join(seen)
        assert "clusterAllReplicas('default', system.query_log)" in joined
        # no un-interpolated placeholder leaked into the SQL
        assert "{self.source" not in joined


@pytest.mark.parametrize(
    "field",
    [
        # Regression for the review finding: these are collected but were not being redacted.
        "source",  # system.dictionaries.source — external-source host/port/user/db
        "command",  # system.mutations.command — raw ALTER/DELETE DDL
        "latest_fail_reason",  # system.mutations.latest_fail_reason — echoes the failing command
        "default_expression",  # system.columns.default_expression — may embed literal values
        # Existing coverage kept for confidence.
        "query",
        "auth_params",
        "select_filter",
        "host_ip",
    ],
)
def test_redact_value_covers_all_sensitive_fields(field):
    """Every declared sensitive top-level column is replaced with [REDACTED] when redaction is on."""
    assert field in SENSITIVE_FIELDS
    assert redact_value(field, "SECRET-VALUE") == "[REDACTED]"
    # A benign quantitative field is never touched.
    assert redact_value("benign_metric", 42) == 42


def test_redact_value_is_top_level_key_match_only():
    """Redaction keys on the exact top-level column name; a nested `source` (e.g.
    actual_billed_cost.source in costs_pricing_config) is passed through unchanged since only
    top-level keys are ever redacted (the extract redacts each row's own keys, not nested ones)."""
    nested = {"source": "cloud_usage_cost_api", "total_usd": 100}
    # The parent key is benign -> the whole nested dict passes through untouched.
    assert redact_value("actual_billed_cost", nested) is nested


def test_safe_query_missing_object_error_is_a_warning_not_an_error():
    """A missing table/column (expected on OSS builds) degrades to an empty result + WARN-level entry."""
    collector = WorkloadCollector(conn=MagicMock(), config={"days_back": 7})
    collector.conn.query = MagicMock(side_effect=Exception("Code: 60. DB::Exception: Table system.foo doesn't exist"))
    rows = collector.safe_query("probe", "SELECT 1")
    assert rows == []
    assert len(collector.errors) == 1
    assert not collector.errors[0].startswith("[ERROR]")


@pytest.mark.parametrize(
    "message",
    [
        "Code: 497. DB::Exception: profiler: Not enough privileges. To execute this query...",  # permission
        "Code: 62. DB::Exception: Syntax error: failed at position 1",  # bad SQL
        "Connection refused",  # transport
    ],
)
def test_safe_query_real_error_is_surfaced_as_error(message):
    """A permission/SQL/connection failure must NOT be masked as a successful empty extract —
    it is recorded as an [ERROR]-tagged entry so it surfaces in the run payload's warnings."""
    collector = WorkloadCollector(conn=MagicMock(), config={"days_back": 7})
    collector.conn.query = MagicMock(side_effect=Exception(message))
    rows = collector.safe_query("probe", "SELECT 1")
    assert rows == []
    assert len(collector.errors) == 1
    assert collector.errors[0].startswith("[ERROR]")


@pytest.mark.parametrize("raw_days_back", [7, "14", "garbage", None])
def test_extract_coerces_days_back_without_aborting(monkeypatch, tmp_path, raw_days_back):
    """execute() must coerce days_back the same way BaseCollector does (fall back to 30) rather than
    aborting the whole extract on a non-numeric credential."""
    creds = {"host": "127.0.0.1", "port": 8123, "profiler": {"days_back": raw_days_back, "redact": True}}
    db_path = _run(monkeypatch, tmp_path, creds)
    # The run completed and produced tables (would have raised on a bare int() of "garbage" / None).
    assert _tables(db_path)
