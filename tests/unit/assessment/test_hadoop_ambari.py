"""Ambari steps: config, cluster resolution, URLs, the step SQL and the component 404 rule.

Fixtures (ambari_*.json): the cluster ``sim`` (two hosts, five services, no Ambari Metrics), and the same hosts before
any service was installed (``ambari_*_empty*``, where the component calls answer 404)."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
import requests
import yaml

from databricks.labs.lakebridge.resources.assessments.hadoop import endpoints, extract
from databricks.labs.lakebridge.resources.assessments.hadoop.ambari import (
    AmbariError,
    component_envelope,
    metric_leaves,
    resolve_ambari_cluster,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import ExtractRun
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
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
_BASE = "http://ambari.example.com:8080"
_CLUSTER = "/api/v1/clusters/sim"

# The original profiler's nine calls, as paths under the Ambari root (its ambariHDFS/DN/RM/NM are the last four).
_ORIGINAL_PATHS = {
    f"{_CLUSTER}?format=blueprint",
    f"{_CLUSTER}/hosts?fields=Hosts/cpu_count,Hosts/disk_info,Hosts/total_mem,Hosts/os_type",
    f"{_CLUSTER}/services",
    f"{_CLUSTER}/hosts?fields=host_components/host_name",
    f"{_CLUSTER}/stack_versions/1",
    f"{_CLUSTER}/services/HDFS/components/NAMENODE?fields=metrics",
    f"{_CLUSTER}/services/HDFS/components/DATANODE?fields=metrics",
    f"{_CLUSTER}/services/YARN/components/RESOURCEMANAGER",
    f"{_CLUSTER}/services/YARN/components/NODEMANAGER",
}


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _sim_routes() -> dict[str, tuple[int, str]]:
    return {
        "/api/v1/clusters": (200, _fixture("ambari_clusters_sim.json")),
        f"{_CLUSTER}?format=blueprint": (200, _fixture("ambari_blueprint_sim.json")),
        f"{_CLUSTER}/hosts?fields=Hosts/cpu_count,Hosts/disk_info,Hosts/total_mem,Hosts/os_type": (
            200,
            _fixture("ambari_hosts_sim.json"),
        ),
        f"{_CLUSTER}/services": (200, _fixture("ambari_services_sim.json")),
        f"{_CLUSTER}/hosts?fields=host_components/host_name": (200, _fixture("ambari_host_components_sim.json")),
        f"{_CLUSTER}/stack_versions/1": (200, _fixture("ambari_stack_versions_sim.json")),
        f"{_CLUSTER}/services/HDFS/components/NAMENODE?fields=metrics": (200, _fixture("ambari_namenode_sim.json")),
        f"{_CLUSTER}/services/HDFS/components/DATANODE?fields=metrics": (200, _fixture("ambari_datanode_sim.json")),
        f"{_CLUSTER}/services/YARN/components/RESOURCEMANAGER": (
            200,
            _fixture("ambari_resourcemanager_sim.json"),
        ),
        f"{_CLUSTER}/services/YARN/components/NODEMANAGER": (200, _fixture("ambari_nodemanager_sim.json")),
    }


def _empty_routes() -> dict[str, tuple[int, str]]:
    """The same Ambari before any service is installed: no services, and the component calls answer 404."""
    routes = _sim_routes()
    routes["/api/v1/clusters"] = (200, _fixture("ambari_clusters_sim.json"))
    routes[f"{_CLUSTER}?format=blueprint"] = (200, _fixture("ambari_blueprint_empty.json"))
    routes[f"{_CLUSTER}/hosts?fields=Hosts/cpu_count,Hosts/disk_info,Hosts/total_mem,Hosts/os_type"] = (
        200,
        _fixture("ambari_hosts_empty.json"),
    )
    routes[f"{_CLUSTER}/services"] = (200, _fixture("ambari_services_empty.json"))
    routes[f"{_CLUSTER}/hosts?fields=host_components/host_name"] = (
        200,
        _fixture("ambari_host_components_empty.json"),
    )
    routes[f"{_CLUSTER}/services/HDFS/components/NAMENODE?fields=metrics"] = (
        404,
        _fixture("ambari_namenode_empty_404.json"),
    )
    routes[f"{_CLUSTER}/services/HDFS/components/DATANODE?fields=metrics"] = (
        404,
        _fixture("ambari_datanode_empty_404.json"),
    )
    routes[f"{_CLUSTER}/services/YARN/components/RESOURCEMANAGER"] = (
        404,
        _fixture("ambari_resourcemanager_empty_404.json"),
    )
    routes[f"{_CLUSTER}/services/YARN/components/NODEMANAGER"] = (404, _fixture("ambari_nodemanager_empty_404.json"))
    return routes


class _FakeSession:
    """A fake requests.Session: answers GETs from a route table and records the URLs."""

    def __init__(self, routes: dict[str, tuple[int, str]]) -> None:
        self._routes = routes
        self.urls: list[str] = []
        self.auth = None
        self.verify: Any = True

    def get(self, url: str, params: Any = None, timeout: Any = None) -> SimpleNamespace:
        assert params is None, params
        del timeout  # the fake answers at once
        self.urls.append(url)
        path = url[len(_BASE) :] if url.startswith(_BASE) else url.split("azurehdinsight.net", 1)[-1]
        if path not in self._routes:
            raise AssertionError(f"unexpected request {url}")
        status, text = self._routes[path]
        return SimpleNamespace(status_code=status, text=text)


def _client(routes: dict[str, tuple[int, str]], base: str = _BASE) -> tuple[RestClient, _FakeSession]:
    session = _FakeSession(routes)
    return RestClient(base, session=session), session  # type: ignore[arg-type]


def _config(**ambari: Any) -> Any:
    return parse_hadoop_config({"distribution": "hdp", "ambari": {"url": _BASE, "auth": "none", **ambari}})


def _load(step: str, bodies: list[str], tmp_path: Path) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / f"{step}.db"
    extract.write_table(
        step=step,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        bodies=bodies,
        params={"distribution": "hdp"},
    )
    return duckdb.connect(str(db_path), read_only=True)


def _one_row(conn: duckdb.DuckDBPyConnection, query: str) -> tuple[Any, ...]:
    row = conn.execute(query).fetchone()
    assert row is not None, query
    return row


def _extract(
    step: str,
    fetch: Callable[[RestClient, Any, ExtractRun], list[str]],
    routes: dict[str, tuple[int, str]],
    tmp_path: Path,
) -> duckdb.DuckDBPyConnection:
    client, _ = _client(routes)
    return _load(step, fetch(client, _config(cluster="sim"), RUN), tmp_path)


# ---- config -------------------------------------------------------------------------------------------------------


def test_ambari_block_parses_url_cluster_and_basic_auth() -> None:
    config = parse_hadoop_config(
        {
            "distribution": "hdp",
            "ambari": {
                "url": "http://ambari.example.com:8080/",
                "auth": "basic",
                "username": "admin",
                "password": "pw",
                "cluster": "  Sandbox HDP  ",
            },
        }
    )

    assert config.ambari is not None
    assert config.ambari.endpoint.urls == ("http://ambari.example.com:8080",)
    assert config.ambari.endpoint.username == "admin"
    assert config.ambari.endpoint.password == "pw"
    assert config.ambari.cluster == "Sandbox HDP"


def test_ambari_absent_is_none() -> None:
    assert parse_hadoop_config({"distribution": "hdp"}).ambari is None


def test_ambari_gateway_url_has_no_port_and_keeps_its_path() -> None:
    config = parse_hadoop_config(
        {"distribution": "hdp", "ambari": {"url": "https://sim.azurehdinsight.net/", "auth": "none"}}
    )

    assert config.ambari is not None
    assert config.ambari.endpoint.urls == ("https://sim.azurehdinsight.net",)
    assert config.ambari.cluster is None


def test_ambari_blank_cluster_means_detect() -> None:
    config = _config(cluster="   ")

    assert config.ambari.cluster is None


def test_ambari_rejects_kerberos_like_cloudera_manager() -> None:
    with pytest.raises(HadoopConfigError, match="kerberos is not supported for ambari"):
        _config(auth="kerberos")


def test_ambari_requires_a_url() -> None:
    with pytest.raises(HadoopConfigError, match="hadoop.ambari.url is required"):
        parse_hadoop_config({"distribution": "hdp", "ambari": {"auth": "none"}})


def test_ambari_rejects_credentials_in_the_url() -> None:
    with pytest.raises(HadoopConfigError, match="put credentials in username and password"):
        parse_hadoop_config({"distribution": "hdp", "ambari": {"url": "http://admin:pw@ambari:8080"}})


def test_ambari_basic_auth_needs_a_username() -> None:
    with pytest.raises(HadoopConfigError, match="needs a username"):
        _config(auth="basic")


def test_ambari_verify_ssl_accepts_a_ca_bundle_path(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("placeholder", encoding="utf-8")

    config = _config(verify_ssl=str(bundle))

    assert config.ambari.endpoint.verify == str(bundle)


# ---- cluster resolution -------------------------------------------------------------------------------------------


def _clusters(*names: str) -> dict[str, tuple[int, str]]:
    body = {"items": [{"Clusters": {"cluster_name": name}} for name in names]}
    return {"/api/v1/clusters": (200, json.dumps(body))}


def test_one_cluster_is_detected_when_none_is_configured() -> None:
    client, _ = _client(_clusters("sim"))

    assert resolve_ambari_cluster(client, None) == "sim"


def test_several_clusters_need_a_configured_name() -> None:
    client, _ = _client(_clusters("prod", "dev"))

    with pytest.raises(AmbariError, match="manages 2 clusters.*'prod', 'dev'"):
        resolve_ambari_cluster(client, None)


def test_no_cluster_is_an_error() -> None:
    client, _ = _client(_clusters())

    with pytest.raises(AmbariError, match="manages no clusters"):
        resolve_ambari_cluster(client, None)


def test_configured_cluster_must_exist() -> None:
    client, _ = _client(_clusters("prod", "dev"))

    assert resolve_ambari_cluster(client, "dev") == "dev"
    with pytest.raises(AmbariError, match="'nope' does not exist.*'prod', 'dev'"):
        resolve_ambari_cluster(client, "nope")


def test_cluster_name_with_spaces_is_url_encoded_in_every_path() -> None:
    routes = _clusters("Sandbox HDP")
    routes["/api/v1/clusters/Sandbox%20HDP?format=blueprint"] = (200, '{"host_groups": [], "Blueprints": {}}')
    client, session = _client(routes)

    endpoints.ambari_blueprint(client, _config(cluster="Sandbox HDP"), RUN)

    assert session.urls[-1] == f"{_BASE}/api/v1/clusters/Sandbox%20HDP?format=blueprint"


def test_gateway_requests_go_to_the_gateway_root_without_a_port() -> None:
    base = "https://sim.azurehdinsight.net"
    routes = {
        "/api/v1/clusters": (200, _fixture("ambari_clusters_sim.json")),
        f"{_CLUSTER}/services": (200, _fixture("ambari_services_sim.json")),
    }
    session = _FakeSession(routes)
    client = RestClient(base, session=session)  # type: ignore[arg-type]

    rows = endpoints.ambari_services(client, _config(cluster="sim", url=base), RUN)

    assert rows == [_fixture("ambari_services_sim.json")]
    assert session.urls == [f"{base}/api/v1/clusters", f"{base}/api/v1/clusters/sim/services"]


def test_extraction_asks_ambari_for_the_original_nine_calls() -> None:
    """Every original call is made, with the original paths and query strings (the cluster list and the service check
    are extra reads this profiler makes)."""
    client, session = _client(_sim_routes())
    config = _config(cluster="sim")
    for fetch in (
        endpoints.ambari_blueprint,
        endpoints.ambari_hosts,
        endpoints.ambari_services,
        endpoints.ambari_host_components,
        endpoints.ambari_stack_versions,
        endpoints.ambari_component_metrics,
    ):
        fetch(client, config, RUN)

    requested = {url[len(_BASE) :] for url in session.urls}
    assert _ORIGINAL_PATHS <= requested
    assert requested - _ORIGINAL_PATHS <= {"/api/v1/clusters", f"{_CLUSTER}/services"}


# ---- component calls: 404 ----------------------------------------------------------------------------------------


def test_component_calls_skip_a_service_the_cluster_does_not_have() -> None:
    """Before any service is installed, the four component calls answer 404 and the step adds no rows."""
    client, _ = _client(_empty_routes())

    assert not endpoints.ambari_component_metrics(client, _config(cluster="sim"), RUN)


def test_a_404_for_a_service_the_cluster_has_is_an_error() -> None:
    routes = _sim_routes()
    routes[f"{_CLUSTER}/services/YARN/components/RESOURCEMANAGER"] = (404, '{"status": 404}')
    client, _ = _client(routes)

    with pytest.raises(HadoopHttpError) as raised:
        endpoints.ambari_component_metrics(client, _config(cluster="sim"), RUN)
    assert raised.value.status_code == 404


def test_a_server_error_on_a_component_is_an_error() -> None:
    routes = _empty_routes()
    routes[f"{_CLUSTER}/services/HDFS/components/NAMENODE?fields=metrics"] = (500, "boom")
    client, _ = _client(routes)

    with pytest.raises(HadoopApiError, match="returned HTTP 500"):
        endpoints.ambari_component_metrics(client, _config(cluster="sim"), RUN)


def test_a_server_error_on_a_top_level_call_is_an_error() -> None:
    routes = _sim_routes()
    routes[f"{_CLUSTER}/hosts?fields=Hosts/cpu_count,Hosts/disk_info,Hosts/total_mem,Hosts/os_type"] = (
        404,
        '{"status": 404}',
    )
    client, _ = _client(routes)

    with pytest.raises(HadoopHttpError):
        endpoints.ambari_hosts(client, _config(cluster="sim"), RUN)


def test_a_non_json_blueprint_is_an_error() -> None:
    routes = _sim_routes()
    routes[f"{_CLUSTER}?format=blueprint"] = (200, "<html>login</html>")
    client, _ = _client(routes)

    with pytest.raises(HadoopApiError, match="did not return JSON"):
        endpoints.ambari_blueprint(client, _config(cluster="sim"), RUN)


def test_a_malformed_component_body_is_a_clear_error() -> None:
    with pytest.raises(AmbariError, match="component body that is not valid JSON"):
        component_envelope('{"metrics": {"cpu": 1')


def test_a_component_body_that_is_not_an_object_has_no_metric_leaves() -> None:
    assert '"metric_leaves":[]' in component_envelope("[]")


# ---- step SQL over the populated capture -------------------------------------------------------------------------


def test_blueprint_has_one_row_per_host_group_and_component(tmp_path: Path) -> None:
    with _extract("ambari_blueprint", endpoints.ambari_blueprint, _sim_routes(), tmp_path) as conn:
        rows = conn.execute(
            "SELECT stack_name, stack_version, host_group_name, cardinality, COUNT(component_name) "
            "FROM ambari_blueprint GROUP BY ALL ORDER BY host_group_name"
        ).fetchall()
        total = _one_row(conn, "SELECT COUNT(*) FROM ambari_blueprint")[0]

    assert rows == [("BIGTOP", "3.3.0", "host_group_1", "1", 15), ("BIGTOP", "3.3.0", "host_group_2", "1", 7)]
    assert total == 22


def test_blueprint_configurations_keep_every_property_value(tmp_path: Path) -> None:
    original = json.loads(_fixture("ambari_blueprint_sim.json"))
    expected = {
        (next(iter(entry)), prop, value)
        for entry in original["configurations"]
        for config_type, body in entry.items()
        for prop, value in body["properties"].items()
    }

    with _extract("ambari_blueprint_configurations", endpoints.ambari_blueprint, _sim_routes(), tmp_path) as conn:
        rows = conn.execute(
            "SELECT config_type, property, property_value FROM ambari_blueprint_configurations"
        ).fetchall()

    assert len(rows) == len(expected) == 24
    assert set(rows) == expected
    assert ("hdfs-site", "dfs.namenode.http-address", "%HOSTGROUP::host_group_1%:50070") in rows


def test_hosts_carry_cpu_memory_os_and_disk_totals(tmp_path: Path) -> None:
    with _extract("ambari_hosts", endpoints.ambari_hosts, _sim_routes(), tmp_path) as conn:
        rows = conn.execute(
            "SELECT cluster_name, host_name, cpu_count, total_mem, os_type, disk_size_total, disk_info "
            "FROM ambari_hosts ORDER BY host_name"
        ).fetchall()

    assert [row[:5] for row in rows] == [
        ("sim", "agent1.ambari.sim", 11, 12187624, "openeuler22"),
        ("sim", "agent2.ambari.sim", 11, 12187624, "openeuler22"),
    ]
    assert rows[0][5] == 474095688
    disks = json.loads(rows[0][6])
    original = json.loads(_fixture("ambari_hosts_sim.json"))
    assert disks == original["items"][0]["Hosts"]["disk_info"]


def test_services_are_the_five_names_in_the_cluster(tmp_path: Path) -> None:
    with _extract("ambari_services", endpoints.ambari_services, _sim_routes(), tmp_path) as conn:
        names = sorted(row[0] for row in conn.execute("SELECT service_name FROM ambari_services").fetchall())

    assert names == ["HDFS", "MAPREDUCE2", "SPARK", "YARN", "ZOOKEEPER"]


def test_host_components_are_one_row_per_host_and_component(tmp_path: Path) -> None:
    with _extract("ambari_host_components", endpoints.ambari_host_components, _sim_routes(), tmp_path) as conn:
        per_host = conn.execute(
            "SELECT host_name, COUNT(*) FROM ambari_host_components GROUP BY host_name ORDER BY host_name"
        ).fetchall()
        total = _one_row(conn, "SELECT COUNT(*) FROM ambari_host_components")[0]

    assert per_host == [("agent1.ambari.sim", 15), ("agent2.ambari.sim", 7)]
    assert total == 22


def test_stack_version_keeps_state_and_host_states(tmp_path: Path) -> None:
    with _extract("ambari_stack_versions", endpoints.ambari_stack_versions, _sim_routes(), tmp_path) as conn:
        row = _one_row(
            conn,
            "SELECT cluster_name, id, stack, version, repository_version, state, supports_revert, host_states, "
            "repository_versions FROM ambari_stack_versions",
        )

    original = json.loads(_fixture("ambari_stack_versions_sim.json"))
    assert row[:7] == ("sim", 1, "BIGTOP", "3.3.0", 1, "OUT_OF_SYNC", False)
    assert json.loads(row[7]) == original["ClusterStackVersions"]["host_states"]
    assert json.loads(row[8]) == original["repository_versions"]


def test_component_metrics_read_the_four_components_from_the_capture(tmp_path: Path) -> None:
    with _extract("ambari_component_metrics", endpoints.ambari_component_metrics, _sim_routes(), tmp_path) as conn:
        rows = conn.execute(
            "SELECT service_name, component_name, state, total_count, installed_count, install_failed_count, "
            "metric_path FROM ambari_component_metrics ORDER BY service_name, component_name"
        ).fetchall()

    # No counts: without Ambari Metrics the NameNode and DataNode have no metrics.
    assert rows == [
        ("HDFS", "DATANODE", None, None, None, None, None),
        ("HDFS", "NAMENODE", None, None, None, None, None),
        ("YARN", "NODEMANAGER", "INSTALLED", 2, 0, 2, None),
        ("YARN", "RESOURCEMANAGER", "INSTALLED", 1, 0, 1, None),
    ]


def test_component_metrics_are_one_row_per_leaf(tmp_path: Path) -> None:
    namenode = json.loads(_fixture("ambari_namenode_sim.json"))
    namenode["metrics"] = {
        "dfs": {"namenode": {"CapacityTotal": 1024, "Name": "nn", "Enabled": True, "Gone": None, "Empty": {}}},
        "jvm": {"HeapUsed": 12.5},
    }
    body = json.dumps(namenode)

    with _load("ambari_component_metrics", [component_envelope(body)], tmp_path) as conn:
        rows = conn.execute(
            "SELECT component_name, metric_path, metric_value FROM ambari_component_metrics ORDER BY metric_path"
        ).fetchall()

    assert rows == [
        ("NAMENODE", "dfs.namenode.CapacityTotal", "1024"),
        ("NAMENODE", "dfs.namenode.Empty", "{}"),
        ("NAMENODE", "dfs.namenode.Enabled", "true"),
        ("NAMENODE", "dfs.namenode.Gone", None),
        ("NAMENODE", "dfs.namenode.Name", "nn"),
        ("NAMENODE", "jvm.HeapUsed", "12.5"),
    ]


def test_metric_leaves_keep_arrays_and_nulls_as_returned() -> None:
    assert metric_leaves({"a": {"b": [1, "x"]}, "c": None, "d": "text"}) == [
        ("a.b", '[1, "x"]'),
        ("c", None),
        ("d", "text"),
    ]
    assert not metric_leaves(None)


# ---- step SQL over the empty-cluster captures --------------------------------------------------------------------


def test_empty_cluster_blueprint_keeps_its_host_group_with_no_component(tmp_path: Path) -> None:
    with _extract("ambari_blueprint", endpoints.ambari_blueprint, _empty_routes(), tmp_path) as conn:
        rows = conn.execute("SELECT host_group_name, cardinality, component_name FROM ambari_blueprint").fetchall()

    assert rows == [("host_group_1", "2", None)]


def test_empty_cluster_has_no_configurations_services_or_components(tmp_path: Path) -> None:
    routes = _empty_routes()
    for step, fetch in (
        ("ambari_blueprint_configurations", endpoints.ambari_blueprint),
        ("ambari_services", endpoints.ambari_services),
        ("ambari_host_components", endpoints.ambari_host_components),
        ("ambari_component_metrics", endpoints.ambari_component_metrics),
    ):
        with _extract(step, fetch, routes, tmp_path) as conn:
            assert _one_row(conn, f"SELECT COUNT(*) FROM {step}")[0] == 0, step


def test_empty_cluster_hosts_have_their_facts_but_no_services(tmp_path: Path) -> None:
    with _extract("ambari_hosts", endpoints.ambari_hosts, _empty_routes(), tmp_path) as conn:
        names = sorted(row[0] for row in conn.execute("SELECT host_name FROM ambari_hosts").fetchall())

    assert names == ["agent1.ambari.sim", "agent2.ambari.sim"]


# ---- the step runner ---------------------------------------------------------------------------------------------


def _patch_session(monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, str]]) -> list[str]:
    """Serve the routes for every requests.Session.get, so the real step runner needs no network."""
    seen: list[str] = []

    def get(_session: Any, url: str, params: Any = None, timeout: Any = None) -> SimpleNamespace:
        del params, timeout  # the fake answers at once
        seen.append(url)
        status, text = routes[url[len(_BASE) :]]
        return SimpleNamespace(status_code=status, text=text)

    monkeypatch.setattr(requests.Session, "get", get)
    return seen


def test_run_step_skips_an_unconfigured_ambari_without_writing(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "hdp"}}))
    db_path = tmp_path / "extract.db"

    message = extract.run_step(
        step="ambari_hosts",
        endpoint="ambari",
        fetch=lambda *_: pytest.fail("no request should be made"),
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    assert message == "skipped: not configured (hadoop.ambari is absent)"
    assert not db_path.exists()


def test_run_step_detects_the_only_cluster_and_writes_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routes = _sim_routes()
    seen = _patch_session(monkeypatch, routes)
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "hdp", "ambari": {"url": _BASE, "auth": "none"}}}))
    db_path = tmp_path / "extract.db"

    message = extract.run_step(
        step="ambari_services",
        endpoint="ambari",
        fetch=endpoints.ambari_services,
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    assert message == "ambari_services extracted 5 rows"
    assert seen == [f"{_BASE}/api/v1/clusters", f"{_BASE}{_CLUSTER}/services"]


def test_run_step_fails_on_several_clusters_without_a_configured_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_session(monkeypatch, _clusters("prod", "dev"))
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "hdp", "ambari": {"url": _BASE, "auth": "none"}}}))

    with pytest.raises(AmbariError, match="manages 2 clusters"):
        extract.run_step(
            step="ambari_services",
            endpoint="ambari",
            fetch=endpoints.ambari_services,
            sql_dir=_SQL_DIR,
            db_path=str(tmp_path / "extract.db"),
            creds_file=creds,
        )


def _run_blueprint_step(step: str, tmp_path: Path) -> str:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "hdp", "ambari": {"url": _BASE, "auth": "none"}}}))
    return extract.run_step(
        step=step,
        endpoint="ambari",
        fetch=endpoints.ambari_blueprint,
        sql_dir=_SQL_DIR,
        db_path=str(tmp_path / "extract.db"),
        creds_file=creds,
    )


def test_the_blueprint_configurations_step_reuses_the_blueprint_step_s_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both blueprint steps read the same ?format=blueprint body; the second takes it from the extract, not Ambari."""
    seen = _patch_session(monkeypatch, _sim_routes())
    _run_blueprint_step("ambari_blueprint", tmp_path)
    requests_by_first_step = len(seen)

    message = _run_blueprint_step("ambari_blueprint_configurations", tmp_path)

    assert message.endswith("rows (from the ambari_blueprint response)")
    assert len(seen) == requests_by_first_step  # no request to Ambari
    with duckdb.connect(str(tmp_path / "extract.db"), read_only=True) as conn:
        bodies = conn.execute(
            "SELECT step, CAST(body AS VARCHAR) FROM hadoop_raw_responses WHERE step LIKE 'ambari_blueprint%' ORDER BY step"
        ).fetchall()
        rows = conn.execute("SELECT COUNT(*) FROM ambari_blueprint_configurations").fetchone()
    assert [step for step, _ in bodies] == ["ambari_blueprint", "ambari_blueprint_configurations"]
    assert bodies[0][1] == bodies[1][1]  # each step still keeps its own raw rows
    assert rows is not None and rows[0] > 0


def test_the_blueprint_configurations_step_fetches_when_the_blueprint_step_stored_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _patch_session(monkeypatch, _sim_routes())

    message = _run_blueprint_step("ambari_blueprint_configurations", tmp_path)

    assert "from the" not in message
    assert f"{_BASE}{_CLUSTER}?format=blueprint" in seen
