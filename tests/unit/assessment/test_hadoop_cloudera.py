"""Cloudera Manager steps: the helpers, the fetch functions against a fake CM, and the step SQL over the fixtures."""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import duckdb
import pytest
import requests

from databricks.labs.lakebridge.resources.assessments.hadoop import endpoints, extract
from databricks.labs.lakebridge.resources.assessments.hadoop.cloudera_manager import (
    TIMESERIES_QUERIES,
    ClouderaManagerError,
    api_root,
    invalid_metric_name,
    item_for_metric,
    metric_expression,
    path_segment,
    resolve_cluster,
    resolve_impala_service,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import ExtractRun
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfig,
    HadoopConfigError,
    parse_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    HadoopApiError,
    HadoopHttpError,
    RestClient,
)

RUN = ExtractRun(run_db="extract.db", initial=True)  # the direct calls below read the initial run

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
_SQL_DIR = Path(extract.__file__).resolve().parent
_CM_URL = "https://cm.example.net:7183"
_CLUSTER = "Cloudera QuickStart"
_FIXED_NOW = datetime(2026, 11, 23, 2, 30, tzinfo=timezone.utc)
_FIRST_WINDOW_FROM = "2026-11-22T00:00:00.000Z"

Handler = Callable[[str, dict], str]


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _config(**cloudera: object) -> HadoopConfig:
    block = {"url": _CM_URL, "username": "profiler", "password": "pw", **cloudera}
    return parse_hadoop_config({"distribution": "cdh", "cloudera_manager": block})


class _Cm:
    """Stands in for RestClient: answers each request from a handler and records the path and parameters."""

    base_url = _CM_URL

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.requests: list[tuple[str, dict]] = []

    def get_plain_text(self, path: str, params: dict | None = None) -> str:
        self.requests.append((path, dict(params or {})))
        return self._handler(path, dict(params or {}))

    def get_text(self, path: str, params: dict | None = None) -> str:
        return self.get_plain_text(path, params)

    def get_json(self, path: str, params: dict | None = None):
        return json.loads(self.get_text(path, params))

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


def _client(handler: Handler) -> RestClient:
    return cast(RestClient, _Cm(handler))


def _standard_cm(path: str, params: dict) -> str:
    """Answers the CM v12 paths the steps use, from the captured fixtures where there is one."""
    if path == "/api/version":
        return "v12\n"
    if path == "/api/v12/clusters":
        return json.dumps({"items": [{"name": _CLUSTER, "displayName": _CLUSTER, "fullVersion": "5.7.0"}]})
    if path == "/api/v12/cm/version":
        return json.dumps({"version": "5.7.0"})
    if path == "/api/v12/clusters/Cloudera%20QuickStart/services":
        return _fixture("cm_services.json")
    if path == "/api/v12/clusters/Cloudera%20QuickStart/export":
        return _fixture("cm_cluster_export.json")
    if path == "/api/v12/hosts":
        return _fixture("cm_hosts.json")
    if path == "/api/v12/cm/allHosts/config":
        return json.dumps({"items": [{"name": "agent_debug", "value": "1", "sensitive": False}]})
    if path == "/api/v12/timeseries":
        return '{"items": []}'
    if path.endswith("/impalaQueries"):
        return '{"queries": []}'
    raise AssertionError(f"unexpected request {path} {params}")


def _load(step: str, bodies: list[str], tmp_path: Path) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "extract.db"
    extract._write_table(  # pylint: disable=protected-access
        step=step,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        bodies=bodies,
        params={"distribution": "cdh"},
        streaming=step == "cm_impala_queries",
    )
    return duckdb.connect(str(db_path), read_only=True)


class _FixedDatetime(datetime):
    """The endpoints' clock, pinned so the windows are the same on every run."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _FIXED_NOW if tz is None else _FIXED_NOW.astimezone(tz)


@pytest.fixture(name="fixed_clock")
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(endpoints, "datetime", _FixedDatetime)


# ---- helpers -------------------------------------------------------------------------------------------------------


def test_api_version_is_read_from_plain_text_and_not_parsed_as_json() -> None:
    fake_cm = _Cm(_standard_cm)

    assert api_root(cast(RestClient, fake_cm), _config().cloudera_manager) == "/api/v12"  # type: ignore[arg-type]
    assert fake_cm.paths() == ["/api/version"]


def test_configured_api_version_skips_detection() -> None:
    fake_cm = _Cm(_standard_cm)

    assert api_root(cast(RestClient, fake_cm), _config(api_version="V11").cloudera_manager) == "/api/v11"  # type: ignore[arg-type]
    assert not fake_cm.requests


@pytest.mark.parametrize("answer", ["<html>Please sign in</html>", "Bad credentials"])
def test_api_version_that_is_not_a_version_is_an_error(answer: str) -> None:
    with pytest.raises(HadoopApiError, match="not an API version"):
        api_root(_client(lambda *_: answer), _config().cloudera_manager)  # type: ignore[arg-type]


def test_cluster_is_detected_when_cm_manages_exactly_one() -> None:
    assert resolve_cluster(_client(_standard_cm), "/api/v12", None) == _CLUSTER


def test_configured_cluster_must_exist() -> None:
    client = _client(_standard_cm)

    assert resolve_cluster(client, "/api/v12", _CLUSTER) == _CLUSTER
    with pytest.raises(ClouderaManagerError, match="'Other' does not exist.*'Cloudera QuickStart'"):
        resolve_cluster(client, "/api/v12", "Other")


def test_several_clusters_without_a_choice_list_them() -> None:
    def two(path: str, params: dict) -> str:
        if path == "/api/v12/clusters":
            return json.dumps({"items": [{"name": "prod"}, {"name": "dev"}]})
        return _standard_cm(path, params)

    with pytest.raises(ClouderaManagerError, match="manages 2 clusters.*'prod', 'dev'"):
        resolve_cluster(_client(two), "/api/v12", None)


def test_no_cluster_is_an_error() -> None:
    with pytest.raises(ClouderaManagerError, match="no clusters"):
        resolve_cluster(_client(lambda *_: '{"items": []}'), "/api/v12", None)


def test_impala_service_is_detected_by_type_and_checked_when_configured() -> None:
    client = _client(_standard_cm)

    assert resolve_impala_service(client, "/api/v12", _CLUSTER, None) == "impala"
    assert resolve_impala_service(client, "/api/v12", _CLUSTER, "impala") == "impala"
    with pytest.raises(ClouderaManagerError, match="Impala service 'impala2' not found"):
        resolve_impala_service(client, "/api/v12", _CLUSTER, "impala2")


def test_impala_service_must_be_unambiguous_when_not_configured() -> None:
    services = {"items": [{"name": "impala", "type": "IMPALA"}, {"name": "impala2", "type": "IMPALA"}]}

    with pytest.raises(ClouderaManagerError, match="2 IMPALA services"):
        resolve_impala_service(_client(lambda *_: json.dumps(services)), "/api/v12", _CLUSTER, None)
    with pytest.raises(ClouderaManagerError, match="has no IMPALA service"):
        resolve_impala_service(_client(lambda *_: '{"items": []}'), "/api/v12", _CLUSTER, None)


def test_cluster_name_with_spaces_is_url_encoded_on_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real RestClient builds the URL, so the encoded segment is what CM would receive."""
    seen: list[str] = []

    def get(_self, url, **_kwargs):
        seen.append(url)
        body = _standard_cm(url.removeprefix(_CM_URL), {})
        return SimpleNamespace(status_code=200, text=body)

    monkeypatch.setattr(requests.Session, "get", get)

    endpoints.cm_services(RestClient(_CM_URL, username="profiler", password="pw"), _config(), RUN)

    assert path_segment(_CLUSTER) == "Cloudera%20QuickStart"
    assert seen[-1] == f"{_CM_URL}/api/v12/clusters/Cloudera%20QuickStart/services"


def test_invalid_metric_error_names_the_metric_and_maps_it_to_its_select_item() -> None:
    error = HadoopHttpError("x", status_code=400, body=_fixture("cm_timeseries_invalid_metric.json"))
    impala_items = next(query for query in TIMESERIES_QUERIES if query.name == "impala_utilization").metrics

    unknown = invalid_metric_name(error)

    assert unknown == "impala_memory_rss"
    assert item_for_metric(unknown, impala_items) == "impala_memory_rss"


def test_an_alias_item_matches_by_its_expression_not_its_alias() -> None:
    cluster_memory = next(query for query in TIMESERIES_QUERIES if query.name == "cluster_memory").metrics

    # The plain metric item comes first, so it is the one a CM error about that metric drops.
    assert item_for_metric("total_physical_memory_used_across_hosts", cluster_memory) == cluster_memory[0]
    # An alias is not a metric, so naming it matches nothing; the percentage item is matched by its expression.
    assert item_for_metric("cluster_memory_percent_usage", cluster_memory) is None
    assert item_for_metric("total_physical_memory_total_across_hosts", cluster_memory) == cluster_memory[1]
    assert metric_expression(cluster_memory[2]).startswith("100*total_physical_memory_used_across_hosts/")


def test_an_invalid_metric_reported_with_http_500_names_the_metric() -> None:
    """CM may answer an unknown metric with HTTP 500 instead of 400; both name the metric."""
    error = HadoopHttpError("x", status_code=500, body=_fixture("cm_timeseries_invalid_metric.json"))

    assert invalid_metric_name(error) == "impala_memory_rss"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, '{"message": "Bad query"}'),  # a 400 that is not about a metric
        (500, '{"message": "Internal server error"}'),  # a server error that is not about a metric
        (404, '{"message": "Invalid metric \'impala_memory_rss\' in \'select ...\'"}'),  # wrong status for it
    ],
)
def test_other_errors_are_not_treated_as_an_invalid_metric(status: int, body: str) -> None:
    assert invalid_metric_name(HadoopHttpError("x", status_code=status, body=body)) is None


# ---- timeseries ----------------------------------------------------------------------------------------------------


def _timeseries_cm(path: str, params: dict) -> str:
    """The captured CM does not know impala_memory_rss or impala_memory_total_used. Every other metric is known."""
    if path != "/api/v12/timeseries":
        return _standard_cm(path, params)
    query = params["query"]
    if "impala_memory_rss" in query:
        raise HadoopHttpError("bad", status_code=400, body=_fixture("cm_timeseries_invalid_metric.json"))
    if "impala_memory_total_used" in query:
        body = json.dumps({"message": "Invalid metric 'impala_memory_total_used' in 'select ...'"})
        raise HadoopHttpError("bad", status_code=400, body=body)
    if query.startswith("select allocated_memory_mb"):
        return _fixture("cm_timeseries_yarn_utilization.json")
    return '{"items": []}'


def test_unknown_metrics_are_dropped_one_at_a_time_and_recorded() -> None:
    fake_cm = _Cm(_timeseries_cm)

    bodies = endpoints.cm_timeseries(cast(RestClient, fake_cm), _config(), RUN)

    impala_queries = [params["query"] for _, params in fake_cm.requests if "impala_query" in params.get("query", "")]
    # The first request fails on impala_memory_rss, the second on impala_memory_total_used, the third succeeds.
    assert len(impala_queries) == 3
    assert "impala_memory_rss" in impala_queries[0] and "impala_memory_total_used" in impala_queries[0]
    assert "impala_memory_rss" not in impala_queries[1] and "impala_memory_total_used" in impala_queries[1]
    assert "impala_memory_rss" not in impala_queries[2] and "impala_memory_total_used" not in impala_queries[2]
    envelopes = {json.loads(body)["query_name"]: json.loads(body) for body in bodies}
    assert envelopes["impala_utilization"]["unsupported_metrics"] == ["impala_memory_rss", "impala_memory_total_used"]
    assert envelopes["impala_utilization"]["response"] == {"items": []}
    assert envelopes["host_roles"]["unsupported_metrics"] == []


def test_an_invalid_metric_error_that_names_no_query_metric_is_raised() -> None:
    def unrelated(path: str, params: dict) -> str:
        if path == "/api/v12/timeseries":
            raise HadoopHttpError("bad", status_code=400, body='{"message": "Invalid metric \'nope\' in \'select\'"}')
        return _standard_cm(path, params)

    with pytest.raises(HadoopHttpError):
        endpoints.cm_timeseries(_client(unrelated), _config(), RUN)


def test_windowed_queries_use_the_hourly_rollup_and_others_have_no_window(fixed_clock: None) -> None:
    fake_cm = _Cm(_standard_cm)

    endpoints.cm_timeseries(cast(RestClient, fake_cm), _config(), RUN)

    host_roles = next(
        params for _, params in fake_cm.requests if params.get("query", "").startswith("select scm_role_state")
    )
    yarn = next(
        params for _, params in fake_cm.requests if params.get("query", "").startswith("select allocated_memory_mb")
    )
    assert "from" not in host_roles and "desiredRollup" not in host_roles
    assert yarn["desiredRollup"] == "HOURLY" and yarn["mustUseDesiredRollup"] == "true"


def test_the_window_is_the_last_month_up_to_today_on_every_run(fixed_clock: None) -> None:
    """The original's date -d '-1 month' to today, for the initial run and an incremental run alike."""
    for run in (RUN, ExtractRun(run_db="later.db", initial=False)):
        fake_cm = _Cm(_standard_cm)

        endpoints.cm_timeseries(cast(RestClient, fake_cm), _config(), run)

        yarn = next(
            params for _, params in fake_cm.requests if params.get("query", "").startswith("select allocated_memory_mb")
        )
        assert (yarn["from"], yarn["to"]) == ("2026-10-23", "2026-11-23")


# ---- impala --------------------------------------------------------------------------------------------------------


def _impala_page(first: int, count: int) -> str:
    queries = [
        {"queryId": f"q{i}", "statement": "select 1", "queryState": "FINISHED"} for i in range(first, first + count)
    ]
    return json.dumps({"queries": queries})


def test_impala_windows_page_until_a_short_page_and_skip_the_rest(fixed_clock: None) -> None:
    """One day of lookback at 60 minutes, with the clock at 02:30 UTC: 26 whole hours and a 30 minute partial hour."""
    calls: list[dict] = []

    def impala(path: str, params: dict) -> str:
        if not path.endswith("/impalaQueries"):
            return _standard_cm(path, params)
        calls.append(params)
        if params["from"] == _FIRST_WINDOW_FROM and params["offset"] == 0:
            return _impala_page(0, 1000)  # a full page: the window asks for the next one
        if params["from"] == _FIRST_WINDOW_FROM and params["offset"] == 1000:
            return _impala_page(995, 10)  # 5 queries seen on the first page, then 5 new ones; short, so the window ends
        return _impala_page(0, 0)

    config = _config(extract_impala_queries=True, impala_lookback_days=1)
    bodies = list(endpoints.cm_impala_queries(_client(impala), config, RUN))

    windows = {(call["from"], call["to"]) for call in calls}
    assert len(windows) == 27
    assert min(windows) == (_FIRST_WINDOW_FROM, "2026-11-22T00:59:59.999Z")
    assert max(windows) == ("2026-11-23T02:00:00.000Z", "2026-11-23T02:29:59.999Z")
    assert [call["offset"] for call in calls if call["from"] == _FIRST_WINDOW_FROM] == [0, 1000]
    assert len(calls) == 27 + 1  # every other window is one request, because its single page is empty
    assert all(call["limit"] == 1000 and call["filter"] == "" for call in calls)
    # The second page's first five queries were already yielded, so only the five new ones come through.
    assert [len(json.loads(body)["queries"]) for body in bodies] == [1000, 5]


def test_impala_queries_come_from_the_resolved_cluster_and_service(fixed_clock: None) -> None:
    fake_cm = _Cm(_standard_cm)

    list(
        endpoints.cm_impala_queries(
            cast(RestClient, fake_cm), _config(extract_impala_queries=True, impala_lookback_days=1), RUN
        )
    )

    assert "/api/v12/clusters/Cloudera%20QuickStart/services/impala/impalaQueries" in fake_cm.paths()


def test_impala_scan_limit_warning_is_logged(fixed_clock: None, caplog: pytest.LogCaptureFixture) -> None:
    def impala(path: str, params: dict) -> str:
        if path.endswith("/impalaQueries"):
            return json.dumps({"queries": [], "warnings": ["Impala query scan limit reached"]})
        return _standard_cm(path, params)

    with caplog.at_level(logging.WARNING):
        list(
            endpoints.cm_impala_queries(
                _client(impala), _config(extract_impala_queries=True, impala_lookback_days=1), RUN
            )
        )

    assert "scan limit" in caplog.text
    assert "reduce hadoop.cloudera_manager.impala_interval_minutes" in caplog.text


def test_impala_truncation_at_the_page_cap_is_logged(fixed_clock: None, caplog: pytest.LogCaptureFixture) -> None:
    def impala(path: str, params: dict) -> str:
        if path.endswith("/impalaQueries"):
            return _impala_page(params["offset"], 1000)
        return _standard_cm(path, params)

    config = _config(extract_impala_queries=True, impala_lookback_days=1, impala_max_pages=1)
    with caplog.at_level(logging.WARNING):
        list(endpoints.cm_impala_queries(_client(impala), config, RUN))

    assert "impala_max_pages=1 allows" in caplog.text


def test_cm_fetch_without_the_block_is_a_config_error() -> None:
    config = parse_hadoop_config({"distribution": "cdh"})

    with pytest.raises(HadoopConfigError, match="not configured"):
        endpoints.cm_services(_client(_standard_cm), config, RUN)


# ---- the step SQL over the captured fixtures -----------------------------------------------------------------------


IMPALA_HEALTH = "BAD"  # the captured impala service's healthSummary


def test_cm_services_rows_and_health_checks(tmp_path: Path) -> None:
    with _load("cm_services", [_fixture("cm_services.json")], tmp_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM cm_services").fetchone()
        impala = conn.execute(
            "SELECT cluster_name, service_type, service_state, health_summary, maintenance_mode "
            "FROM cm_services WHERE service_name = 'impala'"
        ).fetchall()
        hive_checks = conn.execute("SELECT health_checks FROM cm_services WHERE service_name = 'hive'").fetchone()

    assert count == (13,)
    assert impala == [("Cloudera QuickStart", "IMPALA", "STARTED", IMPALA_HEALTH, False)]
    assert hive_checks is not None
    assert json.loads(hive_checks[0])[0]["name"] == "HIVE_HIVEMETASTORES_HEALTHY"


def test_cm_hosts_are_typed(tmp_path: Path) -> None:
    with _load("cm_hosts", [_fixture("cm_hosts.json")], tmp_path) as conn:
        rows = conn.execute("SELECT hostname, num_cores, total_memory_bytes, maintenance_mode FROM cm_hosts").fetchall()

    assert rows == [("quickstart.cloudera", 11, 12480126976, False)]


def test_cm_host_config_reads_items_and_keeps_values(tmp_path: Path) -> None:
    body = json.dumps({"items": [{"name": "agent_debug", "value": "1", "sensitive": False}]})
    with _load("cm_host_config", [body], tmp_path) as conn:
        rows = conn.execute("SELECT config_name, config_value, sensitive FROM cm_host_config").fetchall()

    assert rows == [("agent_debug", "1", False)]


def test_cm_cluster_export_is_flattened_into_sections(tmp_path: Path) -> None:
    with _load("cm_cluster_export", [_fixture("cm_cluster_export.json")], tmp_path) as conn:
        sections = dict(conn.execute("SELECT section, COUNT(*) FROM cm_cluster_export GROUP BY section").fetchall())
        roles = conn.execute(
            "SELECT service_ref, role_type, ref_name FROM cm_cluster_export WHERE section = 'role' ORDER BY ref_name"
        ).fetchall()
        group = conn.execute(
            "SELECT role_config_group_ref, is_base, config_name, config_value FROM cm_cluster_export "
            "WHERE section = 'role_config' AND config_name = 'hbase_indexer_java_heapsize'"
        ).fetchall()

    assert sections == {"service_config": 41, "role_config": 61, "role": 2}
    assert roles[0] == ("hbase", "HBASETHRIFTSERVER", "hbase-HBASETHRIFTSERVER")
    assert group == [("ks_indexer-HBASE_INDEXER-BASE", True, "hbase_indexer_java_heapsize", "52428800")]


def test_cm_clusters_marks_the_selected_cluster(tmp_path: Path) -> None:
    envelope = json.dumps(
        {
            "api_version": "v12",
            "selected_cluster": _CLUSTER,
            "clusters": {
                "items": [{"name": "other", "fullVersion": "5.8.0"}, {"name": _CLUSTER, "fullVersion": "5.7.0"}]
            },
            "cm_version": {"version": "5.7.0"},
        }
    )
    with _load("cm_clusters", [envelope], tmp_path) as conn:
        rows = conn.execute(
            "SELECT cluster_name, full_version, api_version, cm_version, is_selected FROM cm_clusters ORDER BY cluster_name"
        ).fetchall()

    assert rows == [
        ("Cloudera QuickStart", "5.7.0", "v12", "5.7.0", True),
        ("other", "5.8.0", "v12", "5.7.0", False),
    ]


def test_cm_clusters_without_a_selection_marks_none(tmp_path: Path) -> None:
    envelope = json.dumps(
        {"api_version": "v12", "selected_cluster": None, "clusters": {"items": [{"name": "a"}]}, "cm_version": {}}
    )
    with _load("cm_clusters", [envelope], tmp_path) as conn:
        assert conn.execute("SELECT is_selected FROM cm_clusters").fetchall() == [(False,)]


def test_cm_clusters_fetch_selects_the_auto_detected_cluster(tmp_path: Path) -> None:
    """With no cluster configured and one cluster in CM, that cluster is the one the profile is for."""
    (envelope,) = endpoints.cm_clusters(_client(_standard_cm), _config(), RUN)

    assert json.loads(envelope)["selected_cluster"] == _CLUSTER
    with _load("cm_clusters", [envelope], tmp_path) as conn:
        assert conn.execute("SELECT cluster_name, is_selected FROM cm_clusters").fetchall() == [(_CLUSTER, True)]


def test_cm_clusters_fetch_with_an_ambiguous_choice_selects_none() -> None:
    """Several clusters and none configured: the step still lists them all, with no cluster selected."""

    def two_clusters(path: str, params: dict) -> str:
        if path == "/api/v12/clusters":
            return json.dumps({"items": [{"name": "a"}, {"name": "b"}]})
        return _standard_cm(path, params)

    (envelope,) = endpoints.cm_clusters(_client(two_clusters), _config(), RUN)

    assert json.loads(envelope)["selected_cluster"] is None


def test_cm_timeseries_samples_and_unsupported_metrics(tmp_path: Path) -> None:
    yarn = json.loads(_fixture("cm_timeseries_yarn_utilization.json"))
    yarn_body = json.dumps({"query_name": "yarn_utilization", "unsupported_metrics": [], "response": yarn})
    impala_body = json.dumps(
        {"query_name": "impala_utilization", "unsupported_metrics": ["impala_memory_rss", "x"], "response": None}
    )
    with _load("cm_timeseries", [yarn_body, impala_body], tmp_path) as conn:
        dropped = conn.execute(
            "SELECT query_name, metric_name, sample_time, sample_value FROM cm_timeseries "
            "WHERE status = 'unsupported_by_cm' ORDER BY metric_name"
        ).fetchall()
        returned = conn.execute("SELECT DISTINCT query_name FROM cm_timeseries WHERE status = 'returned'").fetchall()

    assert dropped == [
        ("impala_utilization", "impala_memory_rss", None, None),
        ("impala_utilization", "x", None, None),
    ]
    # The captured yarn series have no samples, so only the dropped metrics produce rows.
    assert returned == []


def test_cm_timeseries_points_keep_the_entity_rollup_and_unit(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "query_name": "host_roles",
            "unsupported_metrics": [],
            "response": json.loads(_fixture("cm_timeseries_host_roles.json")),
        }
    )
    with _load("cm_timeseries", [body], tmp_path) as conn:
        uptime = conn.execute(
            "SELECT DISTINCT rollup, unit FROM cm_timeseries WHERE metric_name = 'uptime' AND status = 'returned'"
        ).fetchall()
        service_monitor = conn.execute(
            "SELECT COUNT(*) FROM cm_timeseries WHERE entity_name = 'Service Monitor (quickstart.cloudera)' "
            "AND sample_value IS NOT NULL"
        ).fetchone()

    assert uptime == [("RAW", "seconds")]
    assert service_monitor is not None and service_monitor[0] > 0


def test_cm_impala_queries_are_typed_and_keep_attributes_as_json(tmp_path: Path) -> None:
    with _load("cm_impala_queries", [_fixture("cm_impala_queries.json")], tmp_path) as conn:
        rows = conn.execute(
            "SELECT query_id, query_state, user_name, duration_ms, oom, hdfs_bytes_read, start_time, attributes "
            "FROM cm_impala_queries ORDER BY start_time"
        ).fetchall()

    assert len(rows) == 4
    _, state, user, duration, oom, hdfs_bytes, start_time, attributes = rows[-1]
    assert (state, user, duration, oom, hdfs_bytes) == ("FINISHED", "root", 428, False, 8)
    assert start_time.isoformat() == "2026-11-23T13:47:45.040000"
    assert json.loads(attributes)["pool"] == "default-pool"


def test_streaming_impala_writes_each_page_and_keeps_every_query(tmp_path: Path) -> None:
    full = json.loads(_fixture("cm_impala_queries.json"))
    first = json.dumps({"queries": full["queries"][:2]})
    second = json.dumps({"queries": full["queries"][2:]})

    with _load("cm_impala_queries", [first, second], tmp_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM cm_impala_queries").fetchone()

    assert count == (4,)


# ---- skipping and the step runner ----------------------------------------------------------------------------------


def test_impala_step_is_skipped_without_the_opt_in(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text("hadoop:\n  distribution: cdh\n  cloudera_manager:\n    url: http://cm:7180\n", encoding="utf-8")
    db_path = tmp_path / "extract.db"

    message = extract.run_step(
        step="cm_impala_queries",
        endpoint="impala",
        fetch=lambda *_: pytest.fail("no request should be made"),
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    assert message == "skipped: not configured (hadoop.cloudera_manager.extract_impala_queries is false)"
    assert not db_path.exists()


def test_cm_steps_are_skipped_when_cloudera_manager_is_absent(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text("hadoop:\n  distribution: cdh\n", encoding="utf-8")

    message = extract.run_step(
        step="cm_services",
        endpoint="cloudera_manager",
        fetch=lambda *_: pytest.fail("no request should be made"),
        sql_dir=_SQL_DIR,
        db_path=str(tmp_path / "extract.db"),
        creds_file=creds,
    )

    assert message == "skipped: not configured (hadoop.cloudera_manager is absent)"


def test_ambiguous_cluster_fails_the_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text("hadoop:\n  distribution: cdh\n  cloudera_manager:\n    url: http://cm:7180\n", encoding="utf-8")

    def two_clusters(_self, url, **_kwargs):
        if url.endswith("/api/version"):
            return SimpleNamespace(status_code=200, text="v12")
        return SimpleNamespace(status_code=200, text=json.dumps({"items": [{"name": "a"}, {"name": "b"}]}))

    monkeypatch.setattr(requests.Session, "get", two_clusters)

    with pytest.raises(ClouderaManagerError, match="manages 2 clusters"):
        extract.run_step(
            step="cm_services",
            endpoint="cloudera_manager",
            fetch=endpoints.cm_services,
            sql_dir=_SQL_DIR,
            db_path=str(tmp_path / "extract.db"),
            creds_file=creds,
        )


def test_cm_timeseries_hourly_points_keep_their_aggregate_statistics(tmp_path: Path) -> None:
    point = {
        "timestamp": "2026-11-20T10:00:00.000Z",
        "value": 41.5,
        "type": "CALCULATED",
        "aggregateStatistics": {
            "sampleTime": "2026-11-20T10:59:00.000Z",
            "sampleValue": 40.0,
            "count": 60,
            "min": 12.0,
            "minTime": "2026-11-20T10:03:00.000Z",
            "max": 97.5,
            "maxTime": "2026-11-20T10:41:00.000Z",
            "mean": 41.5,
            "stdDev": 18.25,
            "crossEntityMetadata": {
                "maxEntityDisplayName": "host-2",
                "maxEntityName": "host-2-id",
                "minEntityDisplayName": "host-7",
                "minEntityName": "host-7-id",
                "numEntities": 12.0,
            },
        },
    }
    series = {"metadata": {"metricName": "cpu_percent_across_hosts", "rollupUsed": "HOURLY"}, "data": [point]}
    body = json.dumps(
        {"query_name": "cluster_cpu", "unsupported_metrics": [], "response": {"items": [{"timeSeries": [series]}]}}
    )
    with _load("cm_timeseries", [body], tmp_path) as conn:
        rows = conn.execute(
            "SELECT point_type, sample_value, aggregate_count, aggregate_min, aggregate_min_time, aggregate_max, "
            "aggregate_max_time, aggregate_mean, aggregate_std_dev, cross_entity_count, cross_entity_min_name, "
            "cross_entity_max_name FROM cm_timeseries"
        ).fetchall()

    assert rows == [
        (
            "CALCULATED",
            41.5,
            60,
            12.0,
            datetime(2026, 11, 20, 10, 3),
            97.5,
            datetime(2026, 11, 20, 10, 41),
            41.5,
            18.25,
            12.0,
            "host-7-id",
            "host-2-id",
        )
    ]
