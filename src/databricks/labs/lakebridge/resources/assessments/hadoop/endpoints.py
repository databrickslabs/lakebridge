"""The REST calls each Hadoop step makes. Each returns the response bodies its step's SQL reads.

Cloudera Manager bodies that a step needs to know more about (the API version, the selected cluster, the timeseries
query name) are wrapped in a small JSON envelope built here, so the step's SQL reads one document per request.
"""

import json
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

from databricks.labs.lakebridge.resources.assessments.hadoop.cloudera_manager import (
    TIMESERIES_QUERIES,
    ClouderaManagerError,
    TimeseriesQuery,
    api_root,
    build_timeseries_query,
    invalid_metric_name,
    item_for_metric,
    metric_expression,
    path_segment,
    resolve_cluster,
    resolve_impala_service,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    ClouderaManagerConfig,
    HadoopConfig,
    HadoopConfigError,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.ranger import (
    list_groups,
    list_policies,
    list_service_defs,
    list_services,
    list_users,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopHttpError, RestClient

logger = logging.getLogger(__name__)

YARN_APPS_PATH = "/ws/v1/cluster/apps"
# Applications that have not finished yet. Their finishedTime is 0, so the finished-time window misses them.
YARN_UNFINISHED_STATES = ("NEW", "NEW_SAVING", "SUBMITTED", "ACCEPTED", "RUNNING")
# The page size and the marker CM puts in a response that hit its scan limit (the original profiler's CM_IMPALA_PAGES).
IMPALA_PAGE_LIMIT = 1000
IMPALA_SCAN_LIMIT_MARKER = "Impala query scan limit reached"


def yarn_cluster_info(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text("/ws/v1/cluster/info")]


def yarn_cluster_metrics(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text("/ws/v1/cluster/metrics")]


def yarn_nodes(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text("/ws/v1/cluster/nodes")]


def yarn_scheduler(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text("/ws/v1/cluster/scheduler")]


def yarn_applications(client: RestClient, config: HadoopConfig) -> list[str]:
    """Applications that finished inside the lookback window, plus every application that is still unfinished.

    The two bodies overlap when an application finishes between the two calls. The SQL keeps one row per id.
    """
    since_ms = _epoch_ms_days_ago(config.lookback_days)
    # The user's filter (the original profiler's RM_APP_FILTER) is added to both requests; where it names the same
    # parameter as the window, the user's value wins.
    user_filter = dict(config.yarn.app_filter) if config.yarn else {}
    finished = client.get_text(YARN_APPS_PATH, {"finishedTimeBegin": since_ms, **user_filter})
    unfinished = client.get_text(YARN_APPS_PATH, {"states": ",".join(YARN_UNFINISHED_STATES), **user_filter})
    return [finished, unfinished]


def spark_applications(client: RestClient, config: HadoopConfig) -> list[str]:
    """Spark History Server applications that started inside the lookback window."""
    min_date = (datetime.now(timezone.utc).date() - timedelta(days=config.lookback_days)).isoformat()
    return [client.get_text("/api/v1/applications", {"minDate": min_date})]


# The Impala daemon, catalogd and statestore web UIs answer JSON when the request has a ``json`` parameter. Each body is
# wrapped with the server's URL by the step runner (extract.py), so the step SQL knows which daemon a row came from.
IMPALA_QUERIES_PATH = "/queries?json"
IMPALA_BACKENDS_PATH = "/backends?json"
IMPALA_VARZ_PATH = "/varz?json"
IMPALA_CATALOG_PATH = "/catalog?json"
IMPALA_SUBSCRIBERS_PATH = "/subscribers?json"


def impala_queries(client: RestClient, _config: HadoopConfig) -> list[str]:
    """Completed queries (the daemon's query log) and the queries in flight. The daemon keeps the last query_log_size."""
    return [client.get_text(IMPALA_QUERIES_PATH)]


def impala_backends(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text(IMPALA_BACKENDS_PATH)]


def impala_daemon_config(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text(IMPALA_VARZ_PATH)]


def impala_catalog(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text(IMPALA_CATALOG_PATH)]


def impala_statestore(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [client.get_text(IMPALA_SUBSCRIBERS_PATH)]


def _epoch_ms_days_ago(days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)


def cm_clusters(client: RestClient, config: HadoopConfig) -> list[str]:
    """Every cluster Cloudera Manager manages, with the CM version. Never fails on an ambiguous cluster choice."""
    cm = _cm_config(config)
    root = api_root(client, cm)
    clusters = client.get_text(f"{root}/clusters")
    version = client.get_text(f"{root}/cm/version")
    # The cluster the other steps extract: the configured one, or the only one. When the choice is ambiguous the
    # other CM steps fail with a clear message; this step still lists every cluster, with none selected.
    try:
        selected: str | None = resolve_cluster(client, root, cm.cluster)
    except ClouderaManagerError:
        selected = None
    envelope = (
        '{"api_version":'
        + json.dumps(root.rsplit("/", 1)[-1])
        + ',"selected_cluster":'
        + json.dumps(selected)
        + ',"clusters":'
        + clusters
        + ',"cm_version":'
        + version
        + "}"
    )
    return [envelope]


def cm_services(client: RestClient, config: HadoopConfig) -> list[str]:
    cm = _cm_config(config)
    root = api_root(client, cm)
    cluster = resolve_cluster(client, root, cm.cluster)
    return [client.get_text(f"{root}/clusters/{path_segment(cluster)}/services")]


def cm_hosts(client: RestClient, config: HadoopConfig) -> list[str]:
    return [client.get_text(f"{api_root(client, _cm_config(config))}/hosts")]


def cm_host_config(client: RestClient, config: HadoopConfig) -> list[str]:
    return [client.get_text(f"{api_root(client, _cm_config(config))}/cm/allHosts/config")]


def cm_cluster_export(client: RestClient, config: HadoopConfig) -> list[str]:
    cm = _cm_config(config)
    root = api_root(client, cm)
    cluster = resolve_cluster(client, root, cm.cluster)
    return [client.get_text(f"{root}/clusters/{path_segment(cluster)}/export")]


def cm_timeseries(client: RestClient, config: HadoopConfig) -> list[str]:
    """One envelope per timeseries query. Metrics that the CM version does not know are dropped and recorded."""
    root = api_root(client, _cm_config(config))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=config.lookback_days)
    return [_timeseries_envelope(client, root, query, start, end) for query in TIMESERIES_QUERIES]


def cm_impala_queries(client: RestClient, config: HadoopConfig) -> Iterator[str]:
    """Impala queries by time interval and page, one body per page that has queries it has not yielded yet.

    The bodies are yielded lazily so a large extract never sits in memory as one list.
    """
    cm = _cm_config(config)
    root = api_root(client, cm)
    cluster = resolve_cluster(client, root, cm.cluster)
    service = resolve_impala_service(client, root, cluster, cm.impala_service)
    path = f"{root}/clusters/{path_segment(cluster)}/services/{path_segment(service)}/impalaQueries"

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=cm.impala_lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    interval = timedelta(minutes=cm.impala_interval_minutes)
    seen: set[str] = set()
    while window_start < now:
        window_end = min(window_start + interval, now)
        yield from _impala_window(client, path, cm, window_start, window_end, seen)
        window_start = window_end


def _timeseries_envelope(client: RestClient, root: str, query: TimeseriesQuery, start: date, end: date) -> str:
    """Run one timeseries query, dropping any metric CM rejects as unknown, and wrap the result with the dropped ones."""
    window: dict[str, str] = {}
    if query.windowed:
        window = {
            "desiredRollup": "HOURLY",
            "mustUseDesiredRollup": "true",
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
    metrics = list(query.metrics)
    dropped: list[str] = []
    response: str | None = None
    while metrics:
        try:
            response = client.get_text(
                f"{root}/timeseries", {"query": build_timeseries_query(metrics, query.where), **window}
            )
            break
        except HadoopHttpError as error:
            unknown = invalid_metric_name(error)
            item = item_for_metric(unknown, metrics) if unknown else None
            if item is None:
                raise
            logger.warning(f"Cloudera Manager does not know metric '{unknown}'; skipping it in query {query.name}")
            dropped.append(metric_expression(item))
            metrics = [m for m in metrics if m != item]
    return (
        '{"query_name":'
        + json.dumps(query.name)
        + ',"unsupported_metrics":'
        + json.dumps(dropped)
        + ',"response":'
        + (response if response is not None else "null")
        + "}"
    )


def _impala_window(
    client: RestClient,
    path: str,
    cm: ClouderaManagerConfig,
    start: datetime,
    end: datetime,
    seen: set[str],
) -> Iterator[str]:
    """Page through one time window. A short page ends the window; ``impala_max_pages`` caps a busy one."""
    base = {"from": _iso_millis(start), "to": _iso_millis(end - timedelta(milliseconds=1)), "filter": ""}
    for page in range(cm.impala_max_pages):
        text = client.get_text(path, {**base, "limit": IMPALA_PAGE_LIMIT, "offset": page * IMPALA_PAGE_LIMIT})
        if IMPALA_SCAN_LIMIT_MARKER in text:
            logger.warning(
                f"Cloudera Manager hit the Impala query scan limit for {base['from']} to {base['to']}; "
                "reduce hadoop.cloudera_manager.impala_interval_minutes and raise impala_max_pages to read every query"
            )
        queries = json.loads(text).get("queries") or []
        fresh = []
        for query in queries:
            query_id = query.get("queryId") if isinstance(query, dict) else None
            if query_id is not None:
                if query_id in seen:
                    continue
                seen.add(query_id)
            fresh.append(query)
        if fresh:
            yield json.dumps({"queries": fresh})
        if len(queries) < IMPALA_PAGE_LIMIT:
            return
    logger.warning(
        f"More Impala queries than impala_max_pages={cm.impala_max_pages} allows for {base['from']} to {base['to']}; "
        "raise hadoop.cloudera_manager.impala_max_pages to read the rest"
    )


def _iso_millis(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{moment.microsecond // 1000:03d}Z"


def ranger_services(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [json.dumps(list_services(client))]


def ranger_service_defs(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [list_service_defs(client)]


def ranger_policies(client: RestClient, _config: HadoopConfig) -> list[str]:
    """Every policy. The policies step and the policy items step each read this same list."""
    return [json.dumps(list_policies(client))]


def ranger_users(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [json.dumps(list_users(client))]


def ranger_groups(client: RestClient, _config: HadoopConfig) -> list[str]:
    return [json.dumps(list_groups(client))]


def _cm_config(config: HadoopConfig) -> ClouderaManagerConfig:
    if config.cloudera_manager is None:
        raise HadoopConfigError("hadoop.cloudera_manager is not configured")
    return config.cloudera_manager
