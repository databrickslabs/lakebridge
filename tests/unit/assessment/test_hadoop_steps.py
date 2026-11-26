import json
import threading
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
import requests
import yaml

from databricks.labs.lakebridge.resources.assessments.hadoop import extract
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import yarn_applications
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import ExtractRun
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfigError,
    parse_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import RestClient
from tests.unit.assessment.hadoop_support import run_step_script

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
_SQL_DIR = Path(extract.__file__).resolve().parent
_DISTRIBUTION_PARAMS = {"distribution": "cdh"}
RUN = ExtractRun(run_db="extract.db", initial=True)


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _load(step: str, bodies: list[str], tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """Run a step's SQL over the given bodies, as the step script does, and open the resulting extract."""
    db_path = tmp_path / "extract.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # The step script's own write path, without the network.
    extract.write_table(
        step=step,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        bodies=bodies,
        params=_DISTRIBUTION_PARAMS,
    )
    return duckdb.connect(str(db_path), read_only=True)


def _rows(conn: duckdb.DuckDBPyConnection, query: str) -> list[tuple]:
    return conn.execute(query).fetchall()


def test_cluster_info_records_the_distribution_and_ha_state(tmp_path: Path) -> None:
    with _load("yarn_cluster_info", [_fixture("cluster_info_active.json")], tmp_path) as conn:
        rows = _rows(
            conn, "SELECT distribution, cluster_id, ha_state, cluster_state, hadoop_version FROM yarn_cluster_info"
        )

    assert rows == [("cdh", 1795437591388, "ACTIVE", "STARTED", "3.4.1")]


def test_cluster_metrics_vanilla_and_cdh(tmp_path: Path) -> None:
    with _load("yarn_cluster_metrics", [_fixture("yarn_metrics_vanilla.json")], tmp_path) as conn:
        vanilla = _rows(conn, "SELECT apps_submitted, apps_failed, apps_killed, total_mb FROM yarn_cluster_metrics")
    with _load("yarn_cluster_metrics", [_fixture("yarn_metrics_cdh.json")], tmp_path / "cdh") as conn:
        cdh = _rows(conn, "SELECT apps_submitted, total_nodes, decommissioning_nodes FROM yarn_cluster_metrics")

    assert vanilla == [(6, 1, 1, 4096)]
    # The CDH capture does not report decommissioning nodes, so the column is NULL, not an error.
    assert cdh == [(2, 1, None)]


def test_nodes_vanilla_has_utilisation_and_cdh_does_not(tmp_path: Path) -> None:
    with _load("yarn_nodes", [_fixture("yarn_nodes_vanilla.json")], tmp_path) as conn:
        vanilla = _rows(
            conn, "SELECT node_id, state, version, available_memory_mb, round(mem_utilization, 2) FROM yarn_nodes"
        )
    with _load("yarn_nodes", [_fixture("yarn_nodes_cdh.json")], tmp_path / "cdh") as conn:
        cdh = _rows(conn, "SELECT node_id, version, mem_utilization FROM yarn_nodes")

    assert vanilla == [("localhost:43307", "RUNNING", "3.4.1", 4096, 36.54)]
    assert cdh == [("quickstart.cloudera:8041", "2.6.0-cdh5.7.0", None)]


def test_nodes_empty_response_yields_an_empty_table(tmp_path: Path) -> None:
    with _load("yarn_nodes", [_fixture("yarn_nodes_empty.json")], tmp_path) as conn:
        assert _rows(conn, "SELECT COUNT(*) FROM yarn_nodes") == [(0,)]


def test_scheduler_capacity_tree_is_flattened_with_parents(tmp_path: Path) -> None:
    with _load("yarn_scheduler_queues", [_fixture("yarn_scheduler_vanilla.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT scheduler_type, queue_path, parent_queue_path, depth, queue_type, capacity "
            "FROM yarn_scheduler_queues ORDER BY depth, queue_path",
        )

    assert [row[:4] for row in rows] == [
        ("capacityScheduler", "root", None, 0),
        ("capacityScheduler", "root.analytics", "root", 1),
        ("capacityScheduler", "root.default", "root", 1),
    ]
    assert rows[2][4:] == ("capacitySchedulerLeafQueueInfo", pytest.approx(60.000004))


def test_scheduler_fair_tree_is_read_from_child_queues(tmp_path: Path) -> None:
    with _load("yarn_scheduler_queues", [_fixture("yarn_scheduler_cdh.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT queue_path, parent_queue_path, queue_type, scheduling_policy, max_memory_mb "
            "FROM yarn_scheduler_queues ORDER BY depth, queue_path",
        )

    assert rows[0] == ("root", None, None, "fair", 3072)
    assert [row[:3] for row in rows[1:]] == [
        ("root.default", "root", "fairSchedulerLeafQueueInfo"),
        ("root.root", "root", "fairSchedulerLeafQueueInfo"),
    ]


def test_scheduler_fair_tree_reads_hadoop3_wrapped_child_queues(tmp_path: Path) -> None:
    """Fair-scheduler children wrapped as childQueues: {"queue": [...]}."""
    leaf = "fairSchedulerLeafQueueInfo"
    doc = {
        "scheduler": {
            "schedulerInfo": {
                "type": "fairScheduler",
                "rootQueue": {
                    "queueName": "root",
                    "childQueues": {
                        "queue": [
                            {"queueName": "root.default", "type": leaf},
                            {
                                "queueName": "root.etl",
                                "childQueues": {"queue": [{"queueName": "root.etl.daily", "type": leaf}]},
                            },
                        ]
                    },
                },
            }
        }
    }
    with _load("yarn_scheduler_queues", [json.dumps(doc)], tmp_path) as conn:
        rows = _rows(conn, "SELECT queue_path, parent_queue_path, depth FROM yarn_scheduler_queues ORDER BY queue_path")

    assert rows == [
        ("root", None, 0),
        ("root.default", "root", 1),
        ("root.etl", "root", 1),
        ("root.etl.daily", "root.etl", 2),
    ]


def test_scheduler_capacity_children_without_queue_path_get_full_paths(tmp_path: Path) -> None:
    """A scheduler that reports no queuePath for the root or its children, only short names."""
    with _load("yarn_scheduler_queues", [_fixture("yarn_scheduler_hdi_capacity.json")], tmp_path) as conn:
        rows = _rows(conn, "SELECT queue_path, parent_queue_path, depth FROM yarn_scheduler_queues ORDER BY queue_path")

    assert rows == [("root", None, 0), ("root.default", "root", 1), ("root.thriftsvr", "root", 1)]


def test_scheduler_capacity_root_without_queue_path_uses_its_queue_name(tmp_path: Path) -> None:
    """A capacity scheduler that names the root only by queueName."""
    doc = {
        "scheduler": {
            "schedulerInfo": {
                "type": "capacityScheduler",
                "queueName": "root",
                "queues": {"queue": [{"queueName": "default", "queuePath": "root.default", "capacity": 100.0}]},
            }
        }
    }
    with _load("yarn_scheduler_queues", [json.dumps(doc)], tmp_path) as conn:
        rows = _rows(conn, "SELECT queue_path, parent_queue_path, depth FROM yarn_scheduler_queues ORDER BY depth")

    assert rows == [("root", None, 0), ("root.default", "root", 1)]


def test_yarn_applications_vanilla_is_typed_and_keeps_free_text(tmp_path: Path) -> None:
    with _load("yarn_applications", [_fixture("yarn_applications_vanilla.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT application_id, state, final_status, name, diagnostics, finished_time IS NOT NULL, "
            "allocated_mb, memory_seconds FROM yarn_applications ORDER BY application_id",
        )
        text_columns = _rows(
            conn,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'yarn_applications' "
            "AND data_type = 'VARCHAR'",
        )

    assert len(rows) == 6
    by_id = {row[0]: row for row in rows}
    # Like the original profiler, the name and diagnostics are kept as the ResourceManager reports them.
    failed = by_id["application_1795437591388_0007"]
    assert failed[1:4] == ("FAILED", "FAILED", "spark-fails")
    assert failed[4].startswith("Application application_1795437591388_0007 failed")
    # A successful application has empty diagnostics, which become NULL.
    assert by_id["application_1795437591388_0005"][1:5] == ("FINISHED", "SUCCEEDED", "Spark Pi", None)
    assert by_id["application_1795437591388_0005"][5] is True
    # YARN reports -1 for allocation fields that do not apply to a finished application.
    assert by_id["application_1795437591388_0005"][6] is None
    assert by_id["application_1795437591388_0006"][7] == 26175
    assert ("diagnostics",) in text_columns


def test_yarn_applications_keep_one_row_per_id_preferring_the_finished_one(tmp_path: Path) -> None:
    app_id = "application_1795437591388_0010"
    unfinished = json.dumps(
        {
            "apps": {
                "app": [
                    {
                        "id": app_id,
                        "name": "job",
                        "user": "root",
                        "queue": "root.default",
                        "state": "RUNNING",
                        "finalStatus": "UNDEFINED",
                        "finishedTime": 0,
                        "startedTime": 1795437600000,
                    }
                ]
            }
        }
    )
    finished = json.dumps(
        {
            "apps": {
                "app": [
                    {
                        "id": app_id,
                        "name": "job",
                        "user": "root",
                        "queue": "root.default",
                        "state": "FINISHED",
                        "finalStatus": "SUCCEEDED",
                        "finishedTime": 1795437700000,
                        "startedTime": 1795437600000,
                    }
                ]
            }
        }
    )

    # The unfinished response comes first, as it does when the application finishes between the two calls.
    with _load("yarn_applications", [unfinished, finished], tmp_path) as conn:
        rows = _rows(conn, f"SELECT state, final_status FROM yarn_applications WHERE application_id = '{app_id}'")

    assert rows == [("FINISHED", "SUCCEEDED")]


def test_yarn_applications_empty_rm_yields_an_empty_table(tmp_path: Path) -> None:
    with _load("yarn_applications", [_fixture("yarn_applications_empty.json")], tmp_path) as conn:
        assert _rows(conn, "SELECT COUNT(*) FROM yarn_applications") == [(0,)]


def test_spark_applications_summarise_attempts_and_keep_the_name(tmp_path: Path) -> None:
    with _load("spark_applications", [_fixture("spark_applications_vanilla.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT application_id, name, attempt_count, spark_user, spark_version, completed, "
            "started_time < ended_time FROM spark_applications ORDER BY application_id",
        )

    assert len(rows) == 3
    assert all(row[1:] == ("Spark Pi", 1, "root", "3.5.10", True, True) for row in rows)


def test_spark_applications_empty_list_yields_an_empty_table(tmp_path: Path) -> None:
    with _load("spark_applications", [_fixture("spark_applications_cdh_empty.json")], tmp_path) as conn:
        assert _rows(conn, "SELECT COUNT(*) FROM spark_applications") == [(0,)]


def test_spark_attempts_are_one_row_each_and_missing_attempt_id_is_null(tmp_path: Path) -> None:
    with _load("spark_application_attempts", [_fixture("spark_applications_vanilla.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT application_id, attempt_id, duration_ms, completed FROM spark_application_attempts "
            "ORDER BY application_id",
        )

    assert rows == [
        ("application_1795437591388_0004", "1", 5231, True),
        ("application_1795437591388_0005", None, 10353, True),
        ("application_1795437591388_0006", "1", 12328, True),
    ]


def test_running_spark_attempt_has_no_end_time(tmp_path: Path) -> None:
    running = json.dumps(
        [
            {
                "id": "application_1795437591388_0099",
                "name": "streaming",
                "attempts": [
                    {
                        "attemptId": "1",
                        "startTime": "2026-11-23T12:41:26.240GMT",
                        "endTime": "1969-12-31T23:59:59.999GMT",
                        "lastUpdated": "2026-11-23T12:50:00.000GMT",
                        "duration": 0,
                        "sparkUser": "root",
                        "completed": False,
                        "appSparkVersion": "3.5.10",
                        "startTimeEpoch": 1795437686240,
                        "endTimeEpoch": -1,
                        "lastUpdatedEpoch": 1795438200000,
                    }
                ],
            }
        ]
    )
    with _load("spark_applications", [running], tmp_path / "apps") as conn:
        apps = _rows(conn, "SELECT ended_time, completed FROM spark_applications")
    with _load("spark_application_attempts", [running], tmp_path / "attempts") as conn:
        attempts = _rows(conn, "SELECT ended_time, completed FROM spark_application_attempts")

    assert apps == [(None, False)]
    assert attempts == [(None, False)]


def test_extract_tables_are_persisted_without_temporary_helpers(tmp_path: Path) -> None:
    with _load("yarn_nodes", [_fixture("yarn_nodes_vanilla.json")], tmp_path) as conn:
        tables = {name for (name,) in _rows(conn, "SELECT table_name FROM duckdb_tables()")}
        macros = _rows(conn, "SELECT function_name FROM duckdb_functions() WHERE function_name = 'hadoop_records'")

    assert tables == {"yarn_nodes", extract.RAW_RESPONSES_TABLE}
    assert macros == []


def test_every_response_body_is_kept_verbatim_in_order(tmp_path: Path) -> None:
    """The raw table keeps each body exactly, including YARN's duplicate "entry" keys that a JSON parser collapses."""
    duplicate_keys = (
        '{"apps":{"app":[{"id":"application_1_0001","resourceSecondsMap":'
        '{"entry":{"key":"memory-mb","value":"32111"},"entry":{"key":"vcores","value":"33"}}}]}}'
    )
    bodies = [duplicate_keys, '{"apps":null}']
    with _load("yarn_applications", bodies, tmp_path) as conn:
        rows = _rows(conn, f"SELECT step, sequence, CAST(body AS VARCHAR) FROM {extract.RAW_RESPONSES_TABLE}")

    assert rows == [("yarn_applications", 1, duplicate_keys), ("yarn_applications", 2, '{"apps":null}')]


def test_a_step_replaces_only_its_own_raw_responses(tmp_path: Path) -> None:
    nodes, metrics = _fixture("yarn_nodes_vanilla.json"), _fixture("yarn_metrics_vanilla.json")
    _load("yarn_nodes", [nodes], tmp_path).close()
    _load("yarn_cluster_metrics", [metrics], tmp_path).close()
    with _load("yarn_nodes", [nodes, nodes], tmp_path) as conn:
        counts = _rows(conn, f"SELECT step, COUNT(*) FROM {extract.RAW_RESPONSES_TABLE} GROUP BY step ORDER BY step")

    assert counts == [("yarn_cluster_metrics", 1), ("yarn_nodes", 2)]


def test_run_step_skips_an_unconfigured_spark_history_without_writing(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "oth", "yarn": {"urls": ["http://rm:8088"]}}}))
    db_path = tmp_path / "extract.db"

    message = extract.run_step(
        step="spark_applications",
        endpoint="spark_history",
        fetch=lambda *_: pytest.fail("no request should be made"),
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    assert message.startswith("skipped: not configured")
    assert not db_path.exists()


def test_run_step_applies_the_masking_rules_to_the_raw_responses(tmp_path: Path) -> None:
    """hadoop.search_replace_file plays the role of the original profiler's sed.txt: it rewrites every response."""
    rules = tmp_path / "masking.txt"
    rules.write_text("s/Spark Pi/masked-app/g\ns/\\broot\\b/u1/g\n", encoding="utf-8")
    creds = tmp_path / ".credentials.yml"
    hadoop = {"distribution": "oth", "spark_history": {"url": "http://s:18081"}, "search_replace_file": str(rules)}
    creds.write_text(yaml.safe_dump({"hadoop": hadoop}))
    db_path = tmp_path / "extract.db"

    extract.run_step(
        step="spark_applications",
        endpoint="spark_history",
        fetch=lambda *_: [_fixture("spark_applications_vanilla.json")],
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert _rows(conn, "SELECT DISTINCT name, spark_user FROM spark_applications") == [("masked-app", "u1")]


def test_yarn_applications_is_one_request_for_the_full_list_with_the_app_filter() -> None:
    """Like the original's apps${RM_APP_FILTER}: the whole list, with the user's filter, and no time window."""
    calls: list[tuple[str, dict]] = []

    class _RecordingClient:
        def get_text(self, path: str, params: dict | None = None) -> str:
            calls.append((path, dict(params or {})))
            return '{"apps": null}'

    client = _RecordingClient()
    config = parse_hadoop_config(
        {"distribution": "oth", "yarn": {"urls": ["http://rm:8088"], "app_filter": "?user=etl&queue=root.prod"}}
    )

    yarn_applications(client, config, RUN)  # type: ignore[arg-type]  # a recording stand-in for RestClient

    assert calls == [("/ws/v1/cluster/apps", {"user": "etl", "queue": "root.prod"})]


def test_yarn_applications_without_a_filter_asks_for_every_application() -> None:
    calls: list[tuple[str, dict]] = []

    class _RecordingClient:
        def get_text(self, path: str, params: dict | None = None) -> str:
            calls.append((path, dict(params or {})))
            return '{"apps": null}'

    config = parse_hadoop_config({"distribution": "oth", "yarn": {"urls": ["http://rm:8088"]}})

    yarn_applications(_RecordingClient(), config, RUN)  # type: ignore[arg-type]  # a recording stand-in for RestClient

    assert calls == [("/ws/v1/cluster/apps", {})]


def test_run_step_requires_yarn_for_the_yarn_steps(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "oth", "spark_history": {"url": "http://s:18081"}}}))

    with pytest.raises(HadoopConfigError, match="hadoop.yarn is not configured"):
        extract.run_step(
            step="yarn_applications",
            endpoint="yarn",
            fetch=yarn_applications,
            sql_dir=_SQL_DIR,
            db_path=str(tmp_path / "extract.db"),
            creds_file=creds,
        )


class _HadoopHandler(BaseHTTPRequestHandler):
    """Serves the captured fixtures the way the REST endpoints do. `/standby` is a standby ResourceManager."""

    def _get(self) -> None:
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = parts.path
        if path.startswith("/broken/"):
            return self._send(500, "{}")
        if path.startswith("/cm/"):
            return self._cloudera(path.removeprefix("/cm"), query)
        if path == "/standby/yarnui/ws/v1/cluster/info":
            return self._send(200, _fixture("cluster_info_standby.json"))
        if path == "/yarnui/ws/v1/cluster/info":
            return self._send(200, _fixture("cluster_info_active.json"))
        if path == "/yarnui/ws/v1/cluster/metrics":
            return self._send(200, _fixture("yarn_metrics_vanilla.json"))
        if path == "/yarnui/ws/v1/cluster/apps":
            return self._send(200, _fixture("yarn_applications_vanilla.json"))
        if path == "/spark/api/v1/applications":
            return self._send(200, _fixture("spark_applications_vanilla.json"))
        return self._send(404, "{}")

    do_GET = _get  # BaseHTTPRequestHandler's hook name for GET

    def _cloudera(self, path: str, query: dict) -> None:
        """Cloudera Manager from the captured fixtures. That CM does not know the two Impala metrics below."""
        if path == "/api/version":
            return self._send(200, "v12\n", "text/plain")
        if path == "/api/v12/clusters":
            return self._send(200, json.dumps({"items": [{"name": "Cloudera QuickStart"}]}))
        if path == "/api/v12/clusters/Cloudera%20QuickStart/services":
            return self._send(200, _fixture("cm_services.json"))
        if path == "/api/v12/timeseries":
            metric_query = query.get("query", [""])[0]
            if "impala_memory_rss" in metric_query:
                return self._send(400, _fixture("cm_timeseries_invalid_metric.json"))
            if "impala_memory_total_used" in metric_query:
                return self._send(400, json.dumps({"message": "Invalid metric 'impala_memory_total_used' in 'x'"}))
            return self._send(200, '{"items": []}')
        return self._send(404, "{}")

    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:  # keep test output quiet
        return


@pytest.fixture(name="hadoop_server")
def _hadoop_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HadoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _steps_creds(tmp_path: Path, base: str, *, yarn_urls: list[str] | None = None, spark: bool = True) -> Path:
    hadoop: dict = {
        "distribution": "hdp",
        "yarn": {"urls": yarn_urls or [f"{base}/standby/yarnui", f"{base}/yarnui"]},
    }
    if spark:
        hadoop["spark_history"] = {"url": f"{base}/spark"}
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"secret_vault_type": "local", "hadoop": hadoop}), encoding="utf-8")
    return creds


def _run_script(step: str, db_path: Path, creds: Path) -> None:
    run_step_script(step, db_path, creds)


def test_pipeline_runs_yarn_steps_against_a_gateway_with_standby_first(hadoop_server: str, tmp_path: Path) -> None:
    creds = _steps_creds(tmp_path, hadoop_server)
    db_path = tmp_path / "extract.db"

    _run_script("yarn_cluster_info", db_path, creds)
    _run_script("yarn_applications", db_path, creds)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        info = _rows(conn, "SELECT distribution, ha_state FROM yarn_cluster_info")
        apps = _rows(conn, "SELECT COUNT(*) FROM yarn_applications")
    assert info == [("hdp", "ACTIVE")]
    assert apps == [(6,)]


def test_pipeline_skips_spark_when_it_is_not_configured(hadoop_server: str, tmp_path: Path) -> None:
    creds = _steps_creds(tmp_path, hadoop_server, spark=False)
    db_path = tmp_path / "extract.db"

    _run_script("spark_applications", db_path, creds)  # does not raise: the step reports "skipped"

    assert not db_path.exists()


def test_pipeline_reports_a_failing_rest_endpoint_as_a_step_error(hadoop_server: str, tmp_path: Path) -> None:
    creds = _steps_creds(tmp_path, hadoop_server, yarn_urls=[f"{hadoop_server}/broken/yarnui"])

    with pytest.raises(RuntimeError, match="No ACTIVE ResourceManager found"):
        _run_script("yarn_cluster_metrics", tmp_path / "extract.db", creds)


def test_spark_client_is_a_plain_rest_client_with_no_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Spark History Server has one base URL, so its client is used directly with no ResourceManager lookup."""
    seen: list[str] = []

    def fake_get(_self, url, **_kwargs):
        seen.append(url)
        return SimpleNamespace(status_code=200, text="[]")

    monkeypatch.setattr(requests.Session, "get", fake_get)
    client = RestClient("http://s:18081")

    assert client.get_text("/api/v1/applications", {"minDate": "2026-11-01"}) == "[]"
    assert seen == ["http://s:18081/api/v1/applications"]


def test_pipeline_extracts_cloudera_manager_over_http(hadoop_server: str, tmp_path: Path) -> None:
    """Cloudera steps over real HTTP: the cluster name is URL-encoded and the invalid-metric retry runs."""
    creds = tmp_path / ".credentials.yml"
    cloudera = {"url": f"{hadoop_server}/cm", "auth": "none"}
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "cdh", "cloudera_manager": cloudera}}))
    db_path = tmp_path / "extract.db"

    _run_script("cm_services", db_path, creds)
    _run_script("cm_timeseries", db_path, creds)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        services = _rows(conn, "SELECT COUNT(*) FROM cm_services")
        dropped = _rows(
            conn, "SELECT metric_name FROM cm_timeseries WHERE status = 'unsupported_by_cm' ORDER BY metric_name"
        )

    assert services == [(13,)]
    assert dropped == [("impala_memory_rss",), ("impala_memory_total_used",)]


def _pairs_keeping_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """json.loads hook: a key that repeats (YARN's "entry") keeps every value, as a list."""
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            previous = out[key]
            out[key] = (previous if isinstance(previous, list) else [previous]) + [value]
        else:
            out[key] = value
    return out


def _resource_map(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    entries = raw.get("entry", [])
    entries = entries if isinstance(entries, list) else [entries]
    return {entry["key"]: int(entry["value"]) for entry in entries}


def test_applications_keep_resource_seconds_per_resource_type(tmp_path: Path) -> None:
    body = _fixture("yarn_applications_vanilla.json")
    apps = json.loads(body, object_pairs_hook=_pairs_keeping_duplicates)["apps"]["app"]
    expected = {
        a["id"]: (_resource_map(a.get("resourceSecondsMap")), _resource_map(a.get("preemptedResourceSecondsMap")))
        for a in apps
    }
    with _load("yarn_applications", [body], tmp_path) as conn:
        rows = _rows(conn, "SELECT application_id, resource_seconds, preempted_resource_seconds FROM yarn_applications")

    got = {app: (json.loads(rs) if rs else None, json.loads(prs) if prs else None) for app, rs, prs in rows}
    assert got == expected
    assert any(len(seconds or {}) >= 2 for seconds, _ in expected.values()), "the fixture must have repeated entries"


def test_nodes_keep_the_host_name_and_measured_utilization(tmp_path: Path) -> None:
    body = _fixture("yarn_nodes_vanilla.json")
    nodes = json.loads(body)["nodes"]["node"]
    expected = {
        n["id"]: (
            n["nodeHostName"],
            n["resourceUtilization"]["nodePhysicalMemoryMB"],
            n["resourceUtilization"]["nodeCPUUsage"],
            n["resourceUtilization"]["aggregatedContainersPhysicalMemoryMB"],
            n["resourceUtilization"]["containersCPUUsage"],
        )
        for n in nodes
    }
    with _load("yarn_nodes", [body], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT node_id, node_host_name, node_physical_memory_mb, node_cpu_usage, "
            "containers_physical_memory_mb, containers_cpu_usage FROM yarn_nodes",
        )

    assert {r[0]: r[1:] for r in rows} == expected


def test_cluster_metrics_keep_utilization_and_partition_totals(tmp_path: Path) -> None:
    body = _fixture("yarn_metrics_vanilla.json")
    metrics = json.loads(body)["clusterMetrics"]
    with _load("yarn_cluster_metrics", [body], tmp_path) as conn:
        row = _rows(
            conn,
            "SELECT utilized_mb, utilized_mb_percent, utilized_virtual_cores_percent, "
            "total_memory_mb_across_partitions, used_vcores_across_partitions FROM yarn_cluster_metrics",
        )

    assert row == [
        (
            metrics["utilizedMB"],
            metrics["utilizedMBPercent"],
            metrics["utilizedVirtualCoresPercent"],
            metrics["totalClusterResourcesAcrossPartition"]["memory"],
            metrics["totalUsedResourcesAcrossPartition"]["vCores"],
        )
    ]


def _capacity_queues(info: dict, parent: str | None, out: dict[str, dict]) -> dict[str, dict]:
    """Every queue of a capacity scheduler tree by path, when the RM names children by their short name."""
    name = info.get("queuePath") or info["queueName"]
    path = name if parent is None or name.startswith(parent + ".") else f"{parent}.{name}"
    out[path] = info
    for child in (info.get("queues") or {}).get("queue") or []:
        _capacity_queues(child, path, out)
    return out


@pytest.mark.parametrize("fixture", ["yarn_scheduler_vanilla.json", "yarn_scheduler_hdi_capacity.json"])
def test_queue_partitions_keep_every_partition_capacity_and_usage(fixture: str, tmp_path: Path) -> None:
    body = _fixture(fixture)
    queues = _capacity_queues(json.loads(body)["scheduler"]["schedulerInfo"], None, {})
    expected = set()
    for path, queue in queues.items():
        usages = {u["partitionName"]: u for u in queue.get("resources", {}).get("resourceUsagesByPartition", [])}
        for cap in queue.get("capacities", {}).get("queueCapacitiesByPartition", []):
            use = usages.get(cap["partitionName"], {})
            expected.add(
                (
                    path,
                    cap["partitionName"],
                    cap["absoluteCapacity"],
                    cap.get("absoluteUsedCapacity"),
                    (cap.get("effectiveMaxResource") or {}).get("memory"),
                    (use.get("used") or {}).get("memory"),
                    (use.get("amLimit") or {}).get("vCores"),
                )
            )
    with _load("yarn_scheduler_queue_partitions", [body], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT queue_path, partition_name, absolute_capacity, absolute_used_capacity, effective_max_memory_mb, "
            "used_memory_mb, am_limit_vcores FROM yarn_scheduler_queue_partitions",
        )

    assert expected, "the fixture must have partitions"
    assert set(rows) == expected
    assert len(rows) == len(expected)


def test_a_fair_scheduler_has_no_partitions_but_keeps_its_fair_shares(tmp_path: Path) -> None:
    body = _fixture("yarn_scheduler_cdh.json")
    with _load("yarn_scheduler_queue_partitions", [body], tmp_path) as conn:
        partitions = _rows(conn, "SELECT COUNT(*) FROM yarn_scheduler_queue_partitions")
    with _load("yarn_scheduler_queues", [body], tmp_path) as conn:
        shares = _rows(
            conn,
            "SELECT COUNT(*) FROM yarn_scheduler_queues "
            "WHERE fair_share_memory_mb IS NOT NULL AND steady_fair_share_vcores IS NOT NULL",
        )

    assert partitions == [(0,)]
    assert shares[0][0] > 0


# Attempt times reported only as text, with no epoch fields or duration (-1 is 1969-12-31T23:59:59.999GMT). The
# duration then stays NULL, as in the original's output.
_SPARK_TEXT_TIME_APPS = [
    {
        "id": "application_1_0001",
        "name": "fail-2attempts",
        "attempts": [
            {
                "attemptId": "2",
                "startTime": "2026-11-25T10:38:20.500GMT",
                "endTime": "2026-11-25T10:38:40.000GMT",
                "sparkUser": "etl",
                "completed": True,
            },
            {
                "attemptId": "1",
                "startTime": "2026-11-25T10:38:01.000GMT",
                "endTime": "2026-11-25T10:38:19.250GMT",
                "sparkUser": "etl",
                "completed": True,
            },
        ],
    },
    {
        "id": "application_1_0002",
        "name": "still-running",
        "attempts": [
            {
                "attemptId": "1",
                "startTime": "2026-11-25T10:39:13.015GMT",
                "endTime": "1969-12-31T23:59:59.999GMT",
                "sparkUser": "etl",
                "completed": False,
            },
        ],
    },
]


def _at(hour: int, minute: int, second: int, millis: int) -> datetime:
    return datetime(2026, 11, 25, hour, minute, second, millis * 1000)


def test_spark_text_times_become_timestamps_and_no_duration_is_derived(tmp_path: Path) -> None:
    body = json.dumps(_SPARK_TEXT_TIME_APPS)
    with _load("spark_application_attempts", [body], tmp_path) as conn:
        attempts = _rows(
            conn,
            "SELECT application_id, attempt_id, started_time, ended_time, duration_ms, completed "
            "FROM spark_application_attempts ORDER BY application_id, attempt_id",
        )
    with _load("spark_applications", [body], tmp_path / "apps") as conn:
        apps = _rows(
            conn,
            "SELECT application_id, attempt_count, started_time, ended_time, completed FROM spark_applications ORDER BY 1",
        )

    assert attempts == [
        ("application_1_0001", "1", _at(10, 38, 1, 0), _at(10, 38, 19, 250), None, True),
        ("application_1_0001", "2", _at(10, 38, 20, 500), _at(10, 38, 40, 0), None, True),
        ("application_1_0002", "1", _at(10, 39, 13, 15), None, None, False),
    ]
    assert apps == [
        ("application_1_0001", 2, _at(10, 38, 1, 0), _at(10, 38, 40, 0), True),
        ("application_1_0002", 1, _at(10, 39, 13, 15), None, False),
    ]
