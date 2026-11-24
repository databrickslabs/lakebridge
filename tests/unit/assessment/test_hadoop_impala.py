"""The direct Impala extracts (hadoop.impala): the config block, the parsing of Impala's values, the step SQL over the
captured Impala 4.5.3 web UI bodies, the union over several daemons, the skip rules, and the pipeline over HTTP.

The fixtures named ``impala_*.json`` are real captures. ``impala_queries.json`` is one daemon's completed query log (20
queries, 3 in EXCEPTION). ``impala_queries_inflight.json`` is a later capture of another daemon's log (28 completed, 8 in
EXCEPTION) taken while a query was still running (1 in flight).
"""

import json
import re
import sys
import threading
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb
import pytest
import yaml

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.resources.assessments.hadoop import endpoints, extract
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfigError,
    parse_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopApiError

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
_SQL_DIR = Path(extract.__file__).resolve().parent
_DAEMON_A = "http://impalad-a.example.net:25000"
_DAEMON_B = "http://impalad-b.example.net:25000"
_CATALOGD = "http://catalogd.example.net:25020"
_STATESTORED = "http://statestored.example.net:25010"
_EXCEPTION_IDS = {
    "47489bbcd552605f:810c48d000000000",  # SELECT FROM WHERE broken syntax
    "35442c6542fabfc5:6a1e3bac00000000",  # SELECT * FROM sim_table_that_does_not_exist
    "aa44e0bf8afe5310:80bf523000000000",  # CREATE TABLE sim_orders (a re-run that found the table)
}
_IN_FLIGHT_ID = "4242edf0f21b308f:53a0a8a300000000"
_SELECT_ID = "3346b0c93f893d26:d2e0e30700000000"  # a completed four-way CROSS JOIN count


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _envelope(url: str, name: str) -> str:
    """The body the step runner builds for one daemon response."""
    return extract._coordinator_envelope(url, _fixture(name))  # pylint: disable=protected-access


def _load(step: str, bodies: list[str], tmp_path: Path) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "extract.db"
    extract._write_table(  # pylint: disable=protected-access
        step=step,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        bodies=bodies,
        params={"distribution": "oth"},
        streaming=True,
    )
    return duckdb.connect(str(db_path), read_only=True)


def _rows(conn: duckdb.DuckDBPyConnection, query: str) -> list[tuple]:
    return conn.execute(query).fetchall()


def _impala(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"urls": [_DAEMON_A]}
    block.update(overrides)
    return {"distribution": "oth", "impala": block}


# ---- config -------------------------------------------------------------------------------------------------------------


def test_impala_is_absent_by_default() -> None:
    assert parse_hadoop_config({"distribution": "oth"}).impala is None


def test_impala_block_is_parsed_with_its_urls_auth_and_service_urls() -> None:
    config = parse_hadoop_config(
        _impala(
            urls=[_DAEMON_A, f"{_DAEMON_B}/"],
            auth="basic",
            username="profiler",
            password="s3cret",
            catalogd_url=f"{_CATALOGD}/",
            statestored_url="",
            verify_ssl="false",
        )
    )

    assert config.impala is not None
    assert config.impala.endpoint.urls == (_DAEMON_A, _DAEMON_B)
    assert config.impala.endpoint.username == "profiler"
    assert config.impala.endpoint.password == "s3cret"
    assert config.impala.endpoint.verify is False
    assert config.impala.catalogd_url == _CATALOGD
    assert config.impala.statestored_url is None


def test_impala_urls_may_be_a_comma_separated_string() -> None:
    config = parse_hadoop_config(_impala(urls=f"{_DAEMON_A}, {_DAEMON_B}"))

    assert config.impala is not None
    assert config.impala.endpoint.urls == (_DAEMON_A, _DAEMON_B)


@pytest.mark.parametrize(
    ("impala", "message"),
    [
        ({"urls": []}, "hadoop.impala.urls must be a non-empty list of URLs"),
        ({}, "hadoop.impala.urls must be a non-empty list of URLs"),
        ({"urls": [_DAEMON_A], "auth": "kerberos"}, "kerberos is not supported for impala"),
        ({"urls": [_DAEMON_A], "auth": "basic"}, "hadoop.impala: auth 'basic' needs a username"),
        ({"urls": [_DAEMON_A], "catalogd_url": "ftp://catalogd:25020"}, "hadoop.impala.catalogd_url"),
        ({"urls": [_DAEMON_A], "statestored_url": "not a url"}, "hadoop.impala.statestored_url"),
        ({"urls": ["http://user:pw@impalad:25000"]}, "put credentials in username and password"),
        ({"urls": [_DAEMON_A], "verify_ssl": "maybe"}, "hadoop.impala.verify_ssl"),
        ("impalad:25000", "hadoop.impala must be a mapping"),
    ],
)
def test_invalid_impala_settings_are_rejected(impala: object, message: str) -> None:
    with pytest.raises(HadoopConfigError, match=re.escape(message)):
        parse_hadoop_config({"distribution": "oth", "impala": impala})


# ---- parsing Impala's values ------------------------------------------------------------------------------------------


@pytest.fixture(name="macros")
def _macros() -> Iterator[duckdb.DuckDBPyConnection]:
    with duckdb.connect(":memory:") as conn:
        for macro in extract.HELPER_MACROS:
            conn.execute(macro)
        yield conn


@pytest.mark.parametrize(
    ("text", "milliseconds"),
    [
        ("257.995ms", 257.995),
        ("2.475ms", 2.475),
        ("1s156ms", 1156.0),
        ("43s353ms", 43353.0),
        ("0.000ns", 0.0),
        ("0", 0.0),
        ("1m5s", 65000.0),
        ("1h2m3s", 3723000.0),
        ("", None),
        (None, None),
        ("not a duration", None),
    ],
)
def test_impala_duration_is_read_in_milliseconds(macros: duckdb.DuckDBPyConnection, text, milliseconds) -> None:
    assert macros.execute("SELECT impala_duration_ms(?)", [text]).fetchone() == (milliseconds,)


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("4.11 MB", 4309647),
        ("1.44 KB", 1475),
        ("205.00 B", 205),
        ("0", 0),
        ("1.00 GB", 1073741824),
        ("", None),
        ("not a size", None),
    ],
)
def test_impala_size_is_read_in_bytes(macros: duckdb.DuckDBPyConnection, text, size) -> None:
    assert macros.execute("SELECT impala_bytes(?)", [text]).fetchone() == (size,)


@pytest.mark.parametrize(
    ("text", "moment"),
    [
        ("2026-11-24 05:55:06.008", datetime(2026, 11, 24, 5, 55, 6, 8000)),
        ("2026-11-24 05:52:53.851880000", datetime(2026, 11, 24, 5, 52, 53, 851880)),
        ("1970-01-01 00:00:00.000", None),
        ("", None),
    ],
)
def test_impala_timestamp_keeps_microseconds_and_drops_the_unset_sentinel(
    macros: duckdb.DuckDBPyConnection, text, moment
) -> None:
    assert macros.execute("SELECT impala_timestamp(?)", [text]).fetchone() == (moment,)


# ---- queries over the captures --------------------------------------------------------------------------------------


def test_completed_log_of_one_daemon_has_its_twenty_queries(tmp_path: Path) -> None:
    with _load("impala_queries", [_envelope(_DAEMON_A, "impala_queries.json")], tmp_path) as conn:
        counts = _rows(
            conn,
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE state = 'EXCEPTION'), COUNT(*) FILTER (WHERE is_in_flight), "
            "COUNT(DISTINCT coordinator_host) FROM impala_queries",
        )
        exceptions = _rows(conn, "SELECT query_id FROM impala_queries WHERE state = 'EXCEPTION' ORDER BY query_id")

    assert counts == [(20, 3, 0, 1)]
    assert {row[0] for row in exceptions} == _EXCEPTION_IDS


def test_a_completed_query_has_typed_times_durations_sizes_and_its_raw_record(tmp_path: Path) -> None:
    with _load("impala_queries", [_envelope(_DAEMON_A, "impala_queries.json")], tmp_path) as conn:
        row = _rows(
            conn,
            """SELECT coordinator_url, coordinator_host, is_in_flight, stmt_type, user_name, default_db, state,
                      start_time, end_time, duration_ms, queued_ms, waiting_ms, mem_usage_bytes, mem_est_bytes,
                      rows_fetched, bytes_read, bytes_sent, resource_pool, progress, query_json
               FROM impala_queries WHERE query_id = '3346b0c93f893d26:d2e0e30700000000'""",
        )[0]

    assert row[:9] == (
        _DAEMON_A,
        "impalad-a.example.net",
        False,
        "QUERY",
        "impala",
        "default",
        "FINISHED",
        datetime(2026, 11, 24, 5, 55, 5, 789000),
        datetime(2026, 11, 24, 5, 55, 5, 984000),
    )
    assert row[9:] == (195.343, 0.0, 7196.0, 274760, 67255665, 1, 4833, 72, "default-pool", "4 / 4 ( 100%)", row[19])
    assert json.loads(row[19])["stmt"].startswith("SELECT count(*) FROM sim_orders a CROSS JOIN")


def test_in_flight_query_is_flagged_and_has_no_end_time(tmp_path: Path) -> None:
    with _load("impala_queries", [_envelope(_DAEMON_B, "impala_queries_inflight.json")], tmp_path) as conn:
        row = _rows(
            conn,
            "SELECT is_in_flight, end_time, duration_ms, mem_usage_bytes, mem_est_bytes, start_time "
            f"FROM impala_queries WHERE query_id = '{_IN_FLIGHT_ID}'",
        )[0]
        in_flight = _rows(conn, "SELECT COUNT(*) FROM impala_queries WHERE is_in_flight")

    # Impala reports the in-flight query's state as FINISHED and an end time of 1970-01-01, so the list it is in decides.
    assert row == (True, None, 7039.0, 64870, 16829645, datetime(2026, 11, 24, 6, 11, 51, 57000))
    assert in_flight == [(1,)]


def test_inflight_capture_has_twenty_nine_rows_with_eight_exceptions(tmp_path: Path) -> None:
    with _load("impala_queries", [_envelope(_DAEMON_B, "impala_queries_inflight.json")], tmp_path) as conn:
        counts = _rows(conn, "SELECT COUNT(*), COUNT(*) FILTER (WHERE state = 'EXCEPTION') FROM impala_queries")

    assert counts == [(29, 8)]


def test_daemons_are_unioned_and_each_row_keeps_its_daemon(tmp_path: Path) -> None:
    bodies = [_envelope(_DAEMON_A, "impala_queries.json"), _envelope(_DAEMON_B, "impala_queries_inflight.json")]
    with _load("impala_queries", bodies, tmp_path) as conn:
        per_daemon = _rows(conn, "SELECT coordinator_url, COUNT(*) FROM impala_queries GROUP BY 1 ORDER BY 1")
        shared = _rows(
            conn,
            "SELECT COUNT(*) FROM impala_queries WHERE query_id = '47489bbcd552605f:810c48d000000000'",
        )

    assert per_daemon == [(_DAEMON_A, 20), (_DAEMON_B, 29)]
    # Two daemons can each list a query; they are different rows, one per daemon.
    assert shared == [(2,)]


def test_a_query_listed_twice_by_one_daemon_is_kept_once_as_completed(tmp_path: Path) -> None:
    body = json.loads(_fixture("impala_queries.json"))
    moved = next(q for q in body["completed_queries"] if q["query_id"] == _SELECT_ID)
    body["in_flight_queries"] = [dict(moved)]
    bodies = [extract._coordinator_envelope(_DAEMON_A, json.dumps(body))]  # pylint: disable=protected-access

    with _load("impala_queries", bodies, tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT COUNT(*), BOOL_OR(is_in_flight) FROM impala_queries WHERE query_id = '3346b0c93f893d26:d2e0e30700000000'",
        )
        total = _rows(conn, "SELECT COUNT(*) FROM impala_queries")

    assert rows == [(1, False)]
    assert total == [(20,)]


def test_a_daemon_with_an_empty_log_yields_no_rows(tmp_path: Path) -> None:
    empty = json.dumps({"in_flight_queries": [], "completed_queries": [], "num_in_flight_queries": 0})
    bodies = [extract._coordinator_envelope(_DAEMON_A, empty)]  # pylint: disable=protected-access
    with _load("impala_queries", bodies, tmp_path) as conn:
        assert _rows(conn, "SELECT COUNT(*) FROM impala_queries") == [(0,)]


# ---- the other daemon, catalogd and statestore bodies ---------------------------------------------------------------


def test_backends_are_read_with_admission_sizes_and_the_start_time(tmp_path: Path) -> None:
    with _load("impala_backends", [_envelope(_DAEMON_A, "impala_backends.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            """SELECT coordinator_url, address, is_coordinator, is_executor, is_active, executor_groups,
                      admit_mem_limit_bytes, mem_reserved_bytes, admission_slots, process_start_time, version
               FROM impala_backends""",
        )

    assert rows == [
        (
            _DAEMON_A,
            "172.24.0.5:27000",
            True,
            True,
            True,
            "default",
            536503910,
            0,
            11,
            datetime(2026, 11, 24, 5, 52, 53, 851880),
            "4.5.3-RELEASE RELEASE (build b3e78704d824efd8f9ecb884d4aa1c09d4e12017)",
        )
    ]


def test_daemon_config_keeps_the_curated_flags_with_current_and_default_values(tmp_path: Path) -> None:
    with _load("impala_daemon_config", [_envelope(_DAEMON_A, "impala_varz.json")], tmp_path) as conn:
        flags = {
            name: (value, default)
            for name, value, default in _rows(
                conn, "SELECT flag_name, flag_value, default_value FROM impala_daemon_config"
            )
        }

    assert len(flags) == 16
    assert flags["query_log_size"] == ("200", "200")
    assert flags["query_log_size_in_bytes"] == ("2147483648", "2147483648")
    assert flags["enable_workload_mgmt"] == ("true", "false")
    assert flags["mem_limit"] == ("1gb", "80%")
    assert "hostname" not in flags


def test_catalog_lists_each_table_with_its_database(tmp_path: Path) -> None:
    with _load("impala_catalog", [_envelope(_CATALOGD, "impala_catalog.json")], tmp_path) as conn:
        rows = _rows(
            conn, "SELECT coordinator_url, database_name, table_name, fqtn, has_metrics FROM impala_catalog ORDER BY 3"
        )

    assert rows == [
        (_CATALOGD, "default", "sim_customers", "default.sim_customers", True),
        (_CATALOGD, "default", "sim_orders", "default.sim_orders", True),
    ]


def test_statestore_lists_each_subscriber(tmp_path: Path) -> None:
    with _load("impala_statestore", [_envelope(_STATESTORED, "impala_subscribers.json")], tmp_path) as conn:
        rows = _rows(
            conn,
            "SELECT statestore_url, subscriber_id, num_topics, num_priority_topics, secs_since_heartbeat "
            "FROM impala_statestore ORDER BY subscriber_id",
        )

    assert rows == [
        (_STATESTORED, "catalog-server@172.24.0.4:26000", 1, 0, 0.946),
        (_STATESTORED, "impalad@172.24.0.5:27000", 3, 2, 0.184),
    ]


# ---- the step runner: skips and the fetch functions ------------------------------------------------------------------


class _FakeClient:
    base_url = _DAEMON_A

    def __init__(self, body: str) -> None:
        self.paths: list[str] = []
        self._body = body

    def get_text(self, path: str, params: dict | None = None) -> str:
        assert params is None
        self.paths.append(path)
        return self._body


@pytest.mark.parametrize(
    ("fetch", "path"),
    [
        (endpoints.impala_queries, "/queries?json"),
        (endpoints.impala_backends, "/backends?json"),
        (endpoints.impala_daemon_config, "/varz?json"),
        (endpoints.impala_catalog, "/catalog?json"),
        (endpoints.impala_statestore, "/subscribers?json"),
    ],
)
def test_each_impala_fetch_reads_its_json_endpoint(fetch, path: str) -> None:
    client = _FakeClient("{}")
    fetch(client, parse_hadoop_config(_impala()))  # type: ignore[arg-type]

    assert client.paths == [path]


def _creds(tmp_path: Path, hadoop: dict) -> Path:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"secret_vault_type": "local", "hadoop": hadoop}), encoding="utf-8")
    return creds


def _run(step: str, endpoint: str, fetch, creds: Path, db_path: Path) -> str:
    return extract.run_step(
        step=step,
        endpoint=endpoint,  # type: ignore[arg-type]
        fetch=fetch,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )


def test_impala_steps_are_skipped_when_the_block_is_absent(tmp_path: Path) -> None:
    creds = _creds(tmp_path, {"distribution": "oth"})
    db_path = tmp_path / "extract.db"

    message = _run("impala_queries", "impala_daemons", endpoints.impala_queries, creds, db_path)

    assert message == "skipped: not configured (hadoop.impala is absent)"
    assert not db_path.exists()


@pytest.mark.parametrize(
    ("step", "endpoint", "fetch", "key"),
    [
        ("impala_catalog", "impala_catalogd", endpoints.impala_catalog, "catalogd_url"),
        ("impala_statestore", "impala_statestored", endpoints.impala_statestore, "statestored_url"),
    ],
)
def test_service_steps_are_skipped_when_their_url_is_absent(
    tmp_path: Path, step: str, endpoint: str, fetch, key: str
) -> None:
    creds = _creds(tmp_path, {"distribution": "oth", "impala": {"urls": [_DAEMON_A]}})
    db_path = tmp_path / "extract.db"

    message = _run(step, endpoint, fetch, creds, db_path)

    assert message == f"skipped: not configured (hadoop.impala.{key} is absent)"
    assert not db_path.exists()


# ---- the pipeline's step scripts over HTTP ----------------------------------------------------------------------------

_ROUTES = {
    "/a/queries": "impala_queries.json",
    "/b/queries": "impala_queries_inflight.json",
    "/a/backends": "impala_backends.json",
    "/b/backends": "impala_backends.json",
    "/a/varz": "impala_varz.json",
    "/b/varz": "impala_varz.json",
    "/catalogd/catalog": "impala_catalog.json",
    "/statestored/subscribers": "impala_subscribers.json",
}


class _ImpalaHandler(BaseHTTPRequestHandler):
    """Serves the captured bodies at the paths above. `/broken/...` answers HTTP 500, like a daemon that is down."""

    def do_GET(self) -> None:  # noqa: N802  # pylint: disable=invalid-name
        path = urlsplit(self.path).path
        if path.startswith("/broken/"):
            return self._send(500, "{}")
        if path in _ROUTES:
            return self._send(200, _fixture(_ROUTES[path]))
        return self._send(404, "{}")

    def _send(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:  # keep test output quiet
        return


@pytest.fixture(name="impala_server")
def _impala_server() -> Iterator[str]:
    with ThreadingHTTPServer(("127.0.0.1", 0), _ImpalaHandler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()


def _run_script(step: str, db_path: Path, creds: Path) -> None:
    script = Path(extract.__file__).resolve().parent / f"{step}.py"
    PipelineClass._run_python_script(sys.executable, str(script), db_path, creds)  # pylint: disable=protected-access


def test_pipeline_unions_every_listed_daemon_over_http(impala_server: str, tmp_path: Path) -> None:
    creds = _creds(
        tmp_path,
        {
            "distribution": "oth",
            "impala": {
                "urls": [f"{impala_server}/a", f"{impala_server}/b"],
                "catalogd_url": f"{impala_server}/catalogd",
                "statestored_url": f"{impala_server}/statestored",
            },
        },
    )
    db_path = tmp_path / "extract.db"

    _run_script("impala_queries", db_path, creds)
    _run_script("impala_backends", db_path, creds)
    _run_script("impala_daemon_config", db_path, creds)
    _run_script("impala_catalog", db_path, creds)
    _run_script("impala_statestore", db_path, creds)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        queries = _rows(conn, "SELECT COUNT(*), COUNT(DISTINCT coordinator_url) FROM impala_queries")
        backends = _rows(conn, "SELECT COUNT(*) FROM impala_backends")
        flags = _rows(conn, "SELECT COUNT(*) FROM impala_daemon_config")
        tables = _rows(conn, "SELECT COUNT(*) FROM impala_catalog")
        subscribers = _rows(conn, "SELECT COUNT(*) FROM impala_statestore")
    assert queries == [(49, 2)]
    assert backends == [(2,)]
    assert flags == [(32,)]
    assert tables == [(2,)]
    assert subscribers == [(2,)]


def test_pipeline_masks_impala_responses_before_they_are_stored(impala_server: str, tmp_path: Path) -> None:
    creds = _creds(
        tmp_path,
        {
            "distribution": "oth",
            "impala": {"urls": [f"{impala_server}/a"]},
            "mask_rules": [{"pattern": "172\\.24\\.0\\.5", "replacement": "masked-host"}],
        },
    )
    db_path = tmp_path / "extract.db"

    _run_script("impala_backends", db_path, creds)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        addresses = _rows(conn, "SELECT address FROM impala_backends")
    assert addresses == [("masked-host:27000",)]


def test_pipeline_masks_the_coordinator_url_too(impala_server: str, tmp_path: Path) -> None:
    """mask_rules apply to the coordinator URL the profiler records, not only to the daemon's response."""
    creds = _creds(
        tmp_path,
        {
            "distribution": "oth",
            "impala": {"urls": [f"{impala_server}/a"]},
            "mask_rules": [{"pattern": "127\\.0\\.0\\.1", "replacement": "masked-coordinator"}],
        },
    )
    db_path = tmp_path / "extract.db"

    _run_script("impala_backends", db_path, creds)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        urls = {url for (url,) in _rows(conn, "SELECT coordinator_url FROM impala_backends")}
    assert urls and all("masked-coordinator" in url and "127.0.0.1" not in url for url in urls)


def test_pipeline_reports_an_unreachable_daemon_as_a_step_error(impala_server: str, tmp_path: Path) -> None:
    creds = _creds(
        tmp_path, {"distribution": "oth", "impala": {"urls": [f"{impala_server}/a", f"{impala_server}/broken/x"]}}
    )

    with pytest.raises(RuntimeError, match="returned HTTP 500"):
        _run_script("impala_queries", tmp_path / "extract.db", creds)


def test_impala_fetch_reports_a_daemon_error_as_an_api_error() -> None:
    class _Down:
        base_url = _DAEMON_A

        def get_text(self, path: str, params=None) -> str:
            raise HadoopApiError(f"GET {path} from {self.base_url} timed out")

    with pytest.raises(HadoopApiError, match="timed out"):
        endpoints.impala_queries(_Down(), parse_hadoop_config(_impala()))  # type: ignore[arg-type]


def test_the_envelope_names_the_daemon_and_carries_the_body_unchanged() -> None:
    body = _fixture("impala_backends.json")
    wrapped = json.loads(extract._coordinator_envelope(_DAEMON_A, body))  # pylint: disable=protected-access

    assert wrapped["coordinator"] == _DAEMON_A
    assert wrapped["body"] == json.loads(body)
