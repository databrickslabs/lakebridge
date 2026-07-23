import json
from unittest.mock import Mock

import duckdb
import pytest

from databricks.labs.lakebridge.resources.assessments.legacy_synapse.monitoring_metrics_extract import (
    build_resource_id,
    execute,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseMetrics


def test_build_resource_id_uses_server_short_name():
    """Server short name is the first FQDN label; the pool name is the database segment."""
    azure = {"subscription_id": "sub-123", "resource_group": "rg-analytics"}
    resource_id = build_resource_id(azure, "my-dw-server.database.windows.net", "my_pool")
    assert resource_id == (
        "/subscriptions/sub-123/resourceGroups/rg-analytics"
        "/providers/Microsoft.Sql/servers/my-dw-server/databases/my_pool"
    )


def test_get_sql_dw_metrics_queries_standalone_pool_metric_names():
    """The standalone-pool method uses the REST API (lowercase) metric names, not the
    Synapse-workspace pool names, and runs them through the shared fetch_metrics plumbing."""
    client = Mock()
    client.query_resource.return_value = Mock(metrics=[])

    metrics = SynapseMetrics(client)
    df = metrics.get_sql_dw_metrics("/subscriptions/s/resourceGroups/g/providers/Microsoft.Sql/servers/x/databases/d")

    assert df.empty
    _, kwargs = client.query_resource.call_args
    assert kwargs["metric_names"] == [
        "cpu_percent",
        "dwu_consumption_percent",
        "dwu_limit",
        "dwu_used",
        "memory_usage_percent",
        "physical_data_read_percent",
        "local_tempdb_usage_percent",
        "active_queries",
        "queued_queries",
    ]


def _metrics_client_with_one_row():
    """A metrics client whose query returns a single cpu_percent sample, so that
    get_sql_dw_metrics -> fetch_metrics flattens it into one DataFrame row."""
    metric_value = Mock(timestamp="2026-01-01T00:00:00Z", average=42.0, count=1, maximum=42.0, minimum=42.0, total=42.0)
    timeseries = Mock(data=[metric_value])
    metric = Mock(timeseries=[timeseries])
    metric.name = "cpu_percent"
    client = Mock()
    client.query_resource.return_value = Mock(metrics=[metric])
    return client


def _metrics_client_with_no_rows():
    """A metrics client whose query returns no metrics, so get_sql_dw_metrics -> fetch_metrics
    yields an empty DataFrame (an idle pool with no samples in the lookback window)."""
    client = Mock()
    client.query_resource.return_value = Mock(metrics=[])
    return client


def _credential_manager(settings):
    cred_manager = Mock()
    cred_manager.get_credentials.return_value = settings
    return cred_manager


def _last_json_line(captured: str) -> dict:
    """Parse the last non-empty line as JSON, mirroring how pipeline._run_python_script reads a
    script's structured result (output_lines[-1]) after the preceding lines of log output."""
    lines = [line for line in captured.splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_execute_writes_metrics_with_pool_name(tmp_path, capsys):
    """Happy path: execute builds the resource id, flattens the timeseries and writes
    metrics_dedicated_pool_metrics with the pool name prepended."""
    db_path = tmp_path / "profiler_extract.db"
    settings = {
        "server": "my-dw-server.database.windows.net",
        "database": "my_pool",
        "azure": {"subscription_id": "sub-123", "resource_group": "rg-analytics"},
    }

    execute(
        credential_manager=_credential_manager(settings),
        metrics_client_factory=_metrics_client_with_one_row,
        db_path=str(db_path),
    )

    assert _last_json_line(capsys.readouterr().out)["status"] == "success"
    with duckdb.connect(str(db_path)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info('metrics_dedicated_pool_metrics')").fetchall()]
        rows = conn.execute("SELECT pool_name, name FROM metrics_dedicated_pool_metrics").fetchall()
    assert columns[0] == "pool_name"
    assert rows == [("my_pool", "cpu_percent")]


def test_execute_tolerates_empty_metrics(tmp_path, capsys):
    """An idle pool returns no samples: execute reports success and simply writes no metrics
    table. Empty metrics are tolerated, not treated as an error."""
    db_path = tmp_path / "profiler_extract.db"
    settings = {
        "server": "my-dw-server.database.windows.net",
        "database": "my_pool",
        "azure": {"subscription_id": "sub-123", "resource_group": "rg-analytics"},
    }

    execute(
        credential_manager=_credential_manager(settings),
        metrics_client_factory=_metrics_client_with_no_rows,
        db_path=str(db_path),
    )

    assert _last_json_line(capsys.readouterr().out)["status"] == "success"
    with duckdb.connect(str(db_path)) as conn:
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    assert "metrics_dedicated_pool_metrics" not in tables


def test_execute_fails_without_azure_block(tmp_path, capsys):
    """The azure block is required; execute reports a structured error and exits non-zero."""
    db_path = tmp_path / "profiler_extract.db"
    settings = {"server": "my-dw-server.database.windows.net", "database": "my_pool"}
    metrics_client_factory = Mock()

    with pytest.raises(SystemExit) as exc_info:
        execute(
            credential_manager=_credential_manager(settings),
            metrics_client_factory=metrics_client_factory,
            db_path=str(db_path),
        )

    assert exc_info.value.code == 1
    error = _last_json_line(capsys.readouterr().err)
    assert error["status"] == "error"
    assert "Missing Azure settings" in error["message"]
    # We must fail before touching Azure or DuckDB.
    metrics_client_factory.assert_not_called()
    assert not db_path.exists()
