"""Ambari helpers shared by the ``ambari_*`` steps.

The steps make the original profiler's Ambari calls, against ``/api/v1`` or an HDInsight gateway's root. Unlike the
original, the cluster name is URL-encoded and detected when there is one, and an HTTP error is an error (the original
saved it as data), except a 404 for a component whose service is absent.

Metrics come only when requested (NameNode and DataNode, as the original) and with Ambari Metrics installed.
``metric_leaves`` flattens them into rows.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import AmbariConfig, HadoopConfig
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopHttpError, RestClient

logger = logging.getLogger(__name__)

API_ROOT = "/api/v1"
NOT_FOUND = 404
# The original's four component calls, in its order; only the first two ask for metrics.
COMPONENT_CALLS: tuple[tuple[str, str, bool], ...] = (
    ("HDFS", "NAMENODE", True),
    ("HDFS", "DATANODE", True),
    ("YARN", "RESOURCEMANAGER", False),
    ("YARN", "NODEMANAGER", False),
)


class AmbariError(RuntimeError):
    """Ambari cannot be used as configured: no such cluster, or a cluster must be chosen."""


def ambari_config(config: HadoopConfig) -> AmbariConfig:
    if config.ambari is None:
        raise AmbariError("hadoop.ambari is not configured")
    return config.ambari


def cluster_names(client: RestClient) -> list[str]:
    body = client.get_json(f"{API_ROOT}/clusters")
    items = body.get("items") if isinstance(body, dict) else None
    names = []
    for item in items or []:
        clusters = item.get("Clusters") if isinstance(item, dict) else None
        if isinstance(clusters, dict) and clusters.get("cluster_name"):
            names.append(clusters["cluster_name"])
    return names


def resolve_ambari_cluster(client: RestClient, configured: str | None) -> str:
    """The configured cluster if Ambari has it, else the only one; otherwise an error."""
    names = cluster_names(client)
    if configured is not None:
        if configured in names:
            return configured
        raise AmbariError(
            f"Ambari cluster '{configured}' does not exist. Available clusters: {_listing(names)}. "
            "Confirm hadoop.ambari.cluster"
        )
    if len(names) == 1:
        return names[0]
    if not names:
        raise AmbariError(f"Ambari at {client.base_url} manages no clusters")
    raise AmbariError(f"Ambari manages {len(names)} clusters; set hadoop.ambari.cluster to one of: {_listing(names)}")


def cluster_path(cluster: str) -> str:
    """``/api/v1/clusters/<name>`` with the cluster name URL-encoded (``Sandbox HDP`` becomes ``Sandbox%20HDP``)."""
    return f"{API_ROOT}/clusters/{quote(cluster, safe='')}"


def component_path(cluster: str, service: str, component: str) -> str:
    return f"{cluster_path(cluster)}/services/{quote(service, safe='')}/components/{quote(component, safe='')}"


def get_component(client: RestClient, cluster: str, service: str, component: str, *, metrics: bool) -> str | None:
    """One component call, or None on a 404 for a service the cluster lacks. Any other HTTP error is raised."""
    path = component_path(cluster, service, component)
    if metrics:
        path += "?fields=metrics"
    try:
        return client.get_text(path)
    except HadoopHttpError as error:
        if error.status_code != NOT_FOUND:
            raise
        if service in _service_names(client, cluster):
            raise
        logger.info(f"Ambari has no {service} service in cluster '{cluster}'; no {component} rows are written")
        return None


def _service_names(client: RestClient, cluster: str) -> list[str]:
    body = client.get_json(f"{cluster_path(cluster)}/services")
    items = body.get("items") if isinstance(body, dict) else None
    return [
        item["ServiceInfo"]["service_name"]
        for item in items or []
        if isinstance(item, dict)
        and isinstance(item.get("ServiceInfo"), dict)
        and item["ServiceInfo"].get("service_name")
    ]


def metric_leaves(metrics: Any, prefix: str = "") -> list[tuple[str, str | None]]:
    """Flatten nested ``metrics`` into (dotted path, text) pairs, in Ambari's order.

    Non-empty objects recurse; other values are leaves: strings as is, ``null`` as None, others as JSON text.
    """
    if not isinstance(metrics, Mapping):
        return []
    rows: list[tuple[str, str | None]] = []
    for key, value in metrics.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            rows.extend(metric_leaves(value, path))
        else:
            rows.append((path, _leaf_text(value)))
    return rows


def _leaf_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def component_envelope(body: str) -> str:
    """Wrap one component body with its flattened metric leaves."""
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise AmbariError("Ambari returned a component body that is not valid JSON") from e
    metrics = doc.get("metrics") if isinstance(doc, dict) else None
    leaves = [{"metric_path": path, "metric_value": value} for path, value in metric_leaves(metrics)]
    return '{"component":' + body + ',"metric_leaves":' + json.dumps(leaves) + "}"


def _listing(names: Sequence[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)
