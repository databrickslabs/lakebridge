from unittest.mock import Mock

from databricks.labs.lakebridge.resources.assessments.legacy_synapse.monitoring_metrics_extract import (
    _build_resource_id,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseMetrics


def test_build_resource_id_uses_server_short_name():
    """Server short name is the first FQDN label; the pool name is the database segment."""
    azure = {"subscription_id": "sub-123", "resource_group": "rg-analytics"}
    resource_id = _build_resource_id(azure, "my-dw-server.database.windows.net", "my_pool")
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


def test_get_sql_dw_metrics_builds_dataframe_from_timeseries():
    """fetch_metrics flattens the Azure Monitor timeseries into rows."""
    metric_value = Mock(timestamp="2026-01-01T00:00:00Z", average=42.0, count=1, maximum=42.0, minimum=42.0, total=42.0)
    timeseries = Mock(data=[metric_value])
    metric = Mock(timeseries=[timeseries])
    metric.name = "cpu_percent"
    client = Mock()
    client.query_resource.return_value = Mock(metrics=[metric])

    df = SynapseMetrics(client).get_sql_dw_metrics("/subscriptions/s/.../databases/d")

    assert list(df["name"]) == ["cpu_percent"]
    assert df.iloc[0]["average"] == 42.0
