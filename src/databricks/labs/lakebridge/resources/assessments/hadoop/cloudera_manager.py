"""Cloudera Manager (CM) helpers shared by the ``cm_*`` steps and the Impala step.

Each step resolves the API version, cluster and Impala service itself, so no step depends on another. As the original:
the version comes from plain-text ``GET /api/version``, names are checked before extracting and URL-encoded, and the
timeseries queries use its metric lists. Unlike the original, a query naming a metric CM does not know is retried
without it, and the dropped metric is recorded.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import ClouderaManagerConfig
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    HadoopApiError,
    HadoopHttpError,
    RestClient,
)

API_VERSION_PATH = "/api/version"
_INVALID_METRIC = re.compile(r"Invalid metric '([^']+)'")
_ALIAS = re.compile(r"\s+as\s+", re.IGNORECASE)


class ClouderaManagerError(RuntimeError):
    """Cloudera Manager cannot be used as configured: no such cluster or service, or a cluster must be chosen."""


@dataclass(frozen=True)
class TimeseriesQuery:
    """One ``/api/<v>/timeseries`` request. ``metrics`` are select items, each optionally ``expression as alias``."""

    name: str
    metrics: tuple[str, ...]
    where: str = ""
    # Windowed queries use the original's HOURLY rollup; the host roles and HDFS queries have no window, as there.
    windowed: bool = True


TIMESERIES_QUERIES: tuple[TimeseriesQuery, ...] = (
    TimeseriesQuery(
        name="host_roles",
        metrics=("scm_role_state", "uptime"),
        where="where category=ROLE",
        windowed=False,
    ),
    TimeseriesQuery(
        name="hdfs_usage",
        metrics=(
            "dfs_capacity",
            "dfs_capacity_used",
            "dfs_capacity_free",
            "dfs_capacity_used_non_hdfs",
            "dfs_capacity_across_datanodes",
            "dfs_capacity_used_across_datanodes",
            "dfs_capacity_used_non_hdfs_across_datanodes",
        ),
        windowed=False,
    ),
    TimeseriesQuery(
        name="yarn_utilization",
        metrics=(
            "allocated_memory_mb",
            "allocated_memory_mb_cumulative",
            "available_memory_mb",
            "allocated_memory_gb",
            "available_vcores",
            "allocated_vcores",
            "allocated_vcores_cumulative",
        ),
    ),
    TimeseriesQuery(
        name="yarn_memory_cpu",
        metrics=(
            "yarn_reports_containers_used_vcores",
            "total_allocated_vcores_across_yarn_pools",
            "total_available_vcores_across_yarn_pools as vcores_available",
            "yarn_reports_containers_used_memory",
            "total_available_memory_mb_across_yarn_pools",
            "total_allocated_memory_mb_across_yarn_pools as memory_available",
        ),
    ),
    TimeseriesQuery(
        name="impala_utilization",
        metrics=(
            "queries_successful_rate",
            "queries_ingested_rate",
            "queries_timed_out_rate",
            "queries_rejected_rate",
            "impala_query_thread_cpu_time_rate",
            "impala_query_admission_wait_rate",
            "impala_query_query_duration_rate",
            "impala_query_memory_accrual_rate",
            "mem_rss_across_impalads",
            "total_mem_rss_across_impalads",
            "num_queries_rate_across_impalads",
            "total_num_queries_rate_across_impalads",
            "impala_memory_rss",
            "impala_memory_total_used",
            "total_mem_tracker_process_limit_across_impalads",
            "total_impala_admission_controller_local_backend_mem_reserved_across_impala_daemon_pools",
            "total_impala_admission_controller_local_backend_mem_usage_across_impala_daemon_pools",
        ),
        where="WHERE category=CLUSTER",
    ),
    TimeseriesQuery(
        name="cluster_cpu",
        metrics=("cpu_percent_across_hosts", "total_cores_across_hosts", "total_cpu_percent_across_hosts"),
        where="WHERE category=CLUSTER",
    ),
    TimeseriesQuery(
        name="cluster_memory",
        metrics=(
            "total_physical_memory_used_across_hosts",
            "total_physical_memory_total_across_hosts",
            "100*total_physical_memory_used_across_hosts/total_physical_memory_total_across_hosts "
            "as cluster_memory_percent_usage",
        ),
        where="WHERE category=CLUSTER",
    ),
)


def metric_expression(item: str) -> str:
    """The metric expression of a select item, without its alias: ``a as b`` gives ``a``."""
    return _ALIAS.split(item, maxsplit=1)[0].strip()


def build_timeseries_query(metrics: Sequence[str], where: str) -> str:
    select = "select " + ",".join(dict.fromkeys(metrics))
    return f"{select} {where}" if where else select


def api_root(client: RestClient, config: ClouderaManagerConfig) -> str:
    """Return ``/api/<version>``, from the configured version or from ``GET /api/version`` (plain text)."""
    if config.api_version is not None:
        return f"/api/{config.api_version}"
    version = client.get_plain_text(API_VERSION_PATH).strip()
    if not re.fullmatch(r"v\d+", version):
        raise HadoopApiError(
            f"GET {API_VERSION_PATH} from {client.base_url} returned {version[:40]!r}, not an API version; "
            "check the URL and the credentials"
        )
    return f"/api/{version}"


def json_object(body: Any, path: str, base_url: str) -> dict[str, Any]:
    """The body as a JSON object; anything else (such as a proxy's answer) is an error."""
    if not isinstance(body, dict):
        raise ClouderaManagerError(
            f"GET {path} from {base_url} returned a JSON {type(body).__name__}, not an object; check the Cloudera "
            "Manager URL (a proxy or gateway may answer instead of Cloudera Manager)"
        )
    return body


def cluster_names(client: RestClient, root: str) -> list[str]:
    path = f"{root}/clusters"
    return names_in_clusters_body(client.get_json(path), path, client.base_url)


def names_in_clusters_body(body: Any, path: str, base_url: str) -> list[str]:
    """The cluster names in a parsed ``/clusters`` body."""
    items = json_object(body, path, base_url).get("items") or []
    return [item["name"] for item in items if isinstance(item, dict) and item.get("name")]


def resolve_cluster(client: RestClient, root: str, configured: str | None) -> str:
    """Return the cluster to extract: the configured one if it exists, else the only cluster CM manages."""
    return select_cluster(cluster_names(client, root), configured)


def select_cluster(names: list[str], configured: str | None) -> str:
    """The configured cluster if CM has it, else the only one; otherwise an error."""
    if configured is not None:
        if configured in names:
            return configured
        raise ClouderaManagerError(
            f"Error: '{configured}' does not exist in Cloudera Manager. Available clusters: {_listing(names)}. "
            "Confirm hadoop.cloudera_manager.cluster"
        )
    if len(names) == 1:
        return names[0]
    if not names:
        raise ClouderaManagerError("Cloudera Manager reports no clusters")
    raise ClouderaManagerError(
        f"Cloudera Manager manages {len(names)} clusters; set hadoop.cloudera_manager.cluster to one of: "
        f"{_listing(names)}"
    )


def resolve_impala_service(client: RestClient, root: str, cluster: str, configured: str | None) -> str:
    """Return the Impala service: the configured one if it exists, else the cluster's only IMPALA service."""
    path = f"{root}/clusters/{path_segment(cluster)}/services"
    body = json_object(client.get_json(path), path, client.base_url)
    services = [item for item in (body.get("items") or []) if isinstance(item, dict)]
    if configured is not None:
        if any(service.get("name") == configured for service in services):
            return configured
        raise ClouderaManagerError(
            f"Impala service '{configured}' not found in cluster '{cluster}'. "
            "Confirm hadoop.cloudera_manager.impala_service"
        )
    impala = [service["name"] for service in services if service.get("type") == "IMPALA" and service.get("name")]
    if len(impala) == 1:
        return impala[0]
    if not impala:
        raise ClouderaManagerError(f"Cluster '{cluster}' has no IMPALA service")
    raise ClouderaManagerError(
        f"Cluster '{cluster}' has {len(impala)} IMPALA services; set hadoop.cloudera_manager.impala_service to one "
        f"of: {_listing(impala)}"
    )


def path_segment(value: str) -> str:
    """URL-encode one path segment, such as a cluster name with spaces (``Cloudera QuickStart``)."""
    return quote(value, safe="")


# CM reports an unknown timeseries metric with HTTP 400 or HTTP 500.
_INVALID_METRIC_STATUSES = (400, 500)


def invalid_metric_name(error: HadoopHttpError) -> str | None:
    """The metric named by CM's ``Invalid metric 'x'`` error, or None when the error is something else."""
    if error.status_code not in _INVALID_METRIC_STATUSES:
        return None
    match = _INVALID_METRIC.search(error.body)
    return match.group(1) if match else None


def item_for_metric(metric: str, items: Sequence[str]) -> str | None:
    """The select item that uses ``metric``: the item's expression equals it or contains it as a whole word."""
    for item in items:
        expression = metric_expression(item)
        if expression == metric or re.search(rf"\b{re.escape(metric)}\b", expression):
            return item
    return None


def _listing(names: Sequence[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)
