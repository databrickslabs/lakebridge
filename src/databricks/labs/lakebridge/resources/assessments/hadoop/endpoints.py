"""The REST calls each Hadoop step makes. Each returns the response bodies its step's SQL reads."""

from datetime import datetime, timedelta, timezone

from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import HadoopConfig
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import RestClient

YARN_APPS_PATH = "/ws/v1/cluster/apps"
# Applications that have not finished yet. Their finishedTime is 0, so the finished-time window misses them.
YARN_UNFINISHED_STATES = ("NEW", "NEW_SAVING", "SUBMITTED", "ACCEPTED", "RUNNING")


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


def _epoch_ms_days_ago(days: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
