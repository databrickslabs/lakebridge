import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest

from databricks.labs.lakebridge.resources.assessments.clickhouse import ch_metadata_extract
from databricks.labs.lakebridge.resources.assessments.clickhouse.collectors.workload import WorkloadCollector


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
        return [{"query": "SELECT secret FROM t", "runs": 5, "compute_weight": 100}]

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
    assert value == "[REDACTED]"


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


def test_costs_oss_has_no_dollar_tables(monkeypatch, tmp_path, oss_credentials):
    """OSS: costs reports a resource footprint and no actual-billed-cost (no Cloud API)."""
    db_path = _run(monkeypatch, tmp_path, oss_credentials)
    with duckdb.connect(str(db_path)) as conn:
        pricing_cols = {c[0] for c in conn.execute("DESCRIBE costs_pricing_config").fetchall()}
        footprint = {c[0] for c in conn.execute("DESCRIBE costs_resource_footprint").fetchall()}
    assert "actual_billed_cost" not in pricing_cols
    assert "total_disk_gb" in footprint


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
