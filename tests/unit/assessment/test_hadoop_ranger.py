"""Ranger steps: the v2 reads and paging, the v1 fallback, the step SQL over the captured Ranger 2.9.0 responses, and the
step runner's skip and wiring. The captures are in tests/resources/assessments/hadoop/ranger_*.json."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

from databricks.labs.lakebridge.resources.assessments.hadoop import endpoints, extract, ranger
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import ExtractRun
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import parse_hadoop_config
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import HadoopHttpError, RestClient

RUN = ExtractRun(run_db="extract.db", initial=True)  # the direct calls below read the initial run

_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
_SQL_DIR = Path(extract.__file__).resolve().parent
_RANGER_URL = "https://ranger.example.net:6182"
_V2_SERVICE = "/service/public/v2/api/service"
_V2_POLICY = "/service/public/v2/api/policy"
_V2_SERVICE_DEF = "/service/public/v2/api/servicedef"
_V1_REPOSITORY = "/service/public/api/repository"
_V1_POLICY = "/service/public/api/policy"
_XUSERS_USERS = "/service/xusers/users"
_XUSERS_GROUPS = "/service/xusers/groups"

Handler = Callable[[str, dict], str]


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _fixture_json(name: str) -> Any:
    return json.loads(_fixture(name))


class _Ranger:
    """Stands in for RestClient: answers each GET from a handler and records the path and parameters."""

    base_url = _RANGER_URL

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.requests: list[tuple[str, dict]] = []

    def get_text(self, path: str, params: dict | None = None) -> str:
        request_params = dict(params or {})
        self.requests.append((path, request_params))
        return self._handler(path, request_params)

    def get_json(self, path: str, params: dict | None = None) -> Any:
        return json.loads(self.get_text(path, params))

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


def _client(handler: Handler) -> tuple[RestClient, _Ranger]:
    fake = _Ranger(handler)
    return cast(RestClient, fake), fake


def _not_found(path: str) -> HadoopHttpError:
    return HadoopHttpError(f"GET {path} from {_RANGER_URL} returned HTTP 404", status_code=404, body="")


def _page(rows: list[Any], params: dict, *, cap: int | None = None) -> list[Any]:
    start = int(params.get("startIndex", 0))
    size = int(params.get("pageSize", len(rows)))
    if cap is not None:
        size = min(size, cap)
    return rows[start : start + size]


def _xusers(rows: list[Any], key: str) -> Callable[[dict], str]:
    def answer(params: dict) -> str:
        return json.dumps(
            {key: _page(rows, params), "totalCount": len(rows), "startIndex": params.get("startIndex", 0)}
        )

    return answer


def _ranger_2_9(path: str, params: dict) -> str:
    """Ranger 2.9.0: the v2 API answers every list, and the xusers endpoints are paged."""
    if path == _V2_SERVICE:
        return _fixture("ranger_services_v2.json")
    if path == _V2_POLICY:
        return json.dumps(_page(_fixture_json("ranger_policies_v2.json"), params))
    if path == _V2_SERVICE_DEF:
        return _fixture("ranger_service_defs_v2.json")
    if path == _XUSERS_USERS:
        return _xusers(_fixture_json("ranger_xusers_users.json")["vXUsers"], "vXUsers")(params)
    if path == _XUSERS_GROUPS:
        return _xusers(_fixture_json("ranger_xusers_groups.json")["vXGroups"], "vXGroups")(params)
    raise AssertionError(f"unexpected request {path} {params}")


def _config() -> Any:
    return parse_hadoop_config(
        {"distribution": "hdp", "ranger": {"url": _RANGER_URL, "auth": "basic", "username": "admin", "password": "pw"}}
    )


def _load(step: str, bodies: list[str], tmp_path: Path) -> duckdb.DuckDBPyConnection:
    tmp_path.mkdir(parents=True, exist_ok=True)
    write: dict[str, Any] = {
        "step": step,
        "sql_dir": _SQL_DIR,
        "db_path": str(tmp_path / "extract.db"),
        "bodies": bodies,
        "params": {"distribution": "hdp"},
    }
    extract._write_table(**write)  # pylint: disable=protected-access
    return duckdb.connect(write["db_path"], read_only=True)


def _extract(step: str, fetch: Callable[[RestClient, Any, ExtractRun], list[str]], handler: Handler, tmp_path: Path):
    client, _ = _client(handler)
    return _load(step, fetch(client, _config(), RUN), tmp_path)


# ---- v2 reads against the Ranger 2.9.0 capture -------------------------------------------------------------------


def test_services_are_read_from_v2_with_all_four_services(tmp_path: Path) -> None:
    with _extract("ranger_services", endpoints.ranger_services, _ranger_2_9, tmp_path) as conn:
        rows = conn.execute(
            "SELECT name, service_type, is_enabled, tag_service FROM ranger_services ORDER BY id"
        ).fetchall()

    # sim_yarn and sim_tag are the services the v1 API leaves out.
    assert rows == [
        ("sim_hadoop", "hdfs", True, "sim_tag"),
        ("sim_tag", "tag", True, None),
        ("sim_hive", "hive", True, "sim_tag"),
        ("sim_yarn", "yarn", True, "sim_tag"),
    ]


def test_service_configs_are_kept_as_ranger_returns_them(tmp_path: Path) -> None:
    with _extract("ranger_services", endpoints.ranger_services, _ranger_2_9, tmp_path) as conn:
        configs = conn.execute("SELECT configs FROM ranger_services WHERE name = 'sim_hadoop'").fetchone()[0]

    parsed = json.loads(configs)
    # Ranger masks the password itself; the profiler does not redact anything else.
    assert parsed["password"] == "*****"
    assert parsed["fs.default.name"] == "hdfs://ranger-sim-nn:8020"


def test_service_defs_list_access_types_and_resources(tmp_path: Path) -> None:
    with _extract("ranger_service_defs", endpoints.ranger_service_defs, _ranger_2_9, tmp_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM ranger_service_defs").fetchone()
        hdfs = conn.execute(
            "SELECT access_type_count, access_types, resource_names, options FROM ranger_service_defs WHERE name = 'hdfs'"
        ).fetchone()

    assert count == (22,)
    access_count, access_types, resource_names, options = hdfs
    assert access_count == 3
    assert json.loads(access_types) == ["read", "write", "execute"]
    assert json.loads(resource_names) == ["path"]
    assert json.loads(options)["enableDenyAndExceptionsInPolicies"] == "true"


def test_policies_are_one_row_each_with_masking_and_row_filter_mapped(tmp_path: Path) -> None:
    with _extract("ranger_policies", endpoints.ranger_policies, _ranger_2_9, tmp_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM ranger_policies").fetchone()
        by_type = conn.execute(
            "SELECT policy_type, policy_type_code, COUNT(*) FROM ranger_policies GROUP BY ALL ORDER BY policy_type_code"
        ).fetchall()
        by_service = conn.execute(
            "SELECT service, COUNT(*) FROM ranger_policies GROUP BY service ORDER BY service"
        ).fetchall()
        masking = conn.execute("SELECT name, resources FROM ranger_policies WHERE policy_type = 'masking'").fetchall()
        row_filter = conn.execute("SELECT name FROM ranger_policies WHERE policy_type = 'row_filter'").fetchall()

    assert total == (24,)
    assert by_type == [("access", 0, 22), ("masking", 1, 1), ("row_filter", 2, 1)]
    # Every service's policies are present, including the YARN and tag ones that the v1 API drops.
    assert by_service == [("sim_hadoop", 7), ("sim_hive", 13), ("sim_tag", 1), ("sim_yarn", 3)]
    assert [name for name, _ in masking] == ["customers-email-mask"]
    assert json.loads(masking[0][1])["column"]["values"] == ["email"]
    assert row_filter == [("orders-emea-rowfilter",)]


def test_disabled_policies_are_kept_and_flagged(tmp_path: Path) -> None:
    with _extract("ranger_policies", endpoints.ranger_policies, _ranger_2_9, tmp_path) as conn:
        disabled = conn.execute(
            "SELECT name, service FROM ranger_policies WHERE NOT is_enabled ORDER BY name"
        ).fetchall()

    assert disabled == [("legacy-tmp-open", "sim_hadoop"), ("queue-admin", "sim_yarn")]


def test_policy_items_are_flattened_one_row_per_item(tmp_path: Path) -> None:
    with _extract("ranger_policy_items", endpoints.ranger_policies, _ranger_2_9, tmp_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM ranger_policy_items").fetchone()
        kinds = conn.execute(
            "SELECT item_kind, COUNT(*) FROM ranger_policy_items GROUP BY item_kind ORDER BY item_kind"
        ).fetchall()
        mask = conn.execute(
            "SELECT policy_name, mask_type, users, groups FROM ranger_policy_items WHERE item_kind = 'data_mask'"
        ).fetchall()
        row_filter = conn.execute(
            "SELECT policy_name, row_filter_expr, groups FROM ranger_policy_items WHERE item_kind = 'row_filter'"
        ).fetchall()
        deny = conn.execute(
            "SELECT policy_name, groups FROM ranger_policy_items WHERE item_kind = 'deny' ORDER BY policy_name"
        ).fetchall()

    assert total == (31,)
    assert kinds == [("allow", 26), ("data_mask", 1), ("deny", 3), ("row_filter", 1)]
    assert mask == [("customers-email-mask", "MASK_HASH", None, '["analysts"]')]
    assert row_filter == [("orders-emea-rowfilter", "region = 'EMEA'", '["analysts"]')]
    assert {name for name, _ in deny} == {"finance-readers", "EXPIRES_ON", "payroll-hr-only"}


def test_policy_item_accesses_and_delegate_admin_are_kept_as_json(tmp_path: Path) -> None:
    with _extract("ranger_policy_items", endpoints.ranger_policies, _ranger_2_9, tmp_path) as conn:
        hdfs_root = conn.execute(
            "SELECT users, accesses, delegate_admin FROM ranger_policy_items "
            "WHERE policy_name = 'all - path' AND item_kind = 'allow'"
        ).fetchone()

    users, accesses, delegate_admin = hdfs_root
    assert json.loads(users) == ["hdfs"]
    assert [a["type"] for a in json.loads(accesses)] == ["read", "write", "execute"]
    assert delegate_admin is True


def test_users_and_groups_keep_identity_metadata_only(tmp_path: Path) -> None:
    with _extract("ranger_users", endpoints.ranger_users, _ranger_2_9, tmp_path) as conn:
        columns = {row[0] for row in conn.execute("DESCRIBE ranger_users").fetchall()}
        users = conn.execute("SELECT COUNT(*) FROM ranger_users").fetchone()
        analyst = conn.execute("SELECT name, group_names, user_source FROM ranger_users WHERE name = 'alice_analyst'")
        analyst_row = analyst.fetchone()
    with _extract("ranger_groups", endpoints.ranger_groups, _ranger_2_9, tmp_path / "groups") as conn:
        groups = conn.execute("SELECT COUNT(*) FROM ranger_groups").fetchone()
        group_columns = {row[0] for row in conn.execute("DESCRIBE ranger_groups").fetchall()}

    assert users == (12,)
    assert analyst_row == ("alice_analyst", '["analysts"]', 0)
    assert columns == {
        "id",
        "name",
        "status",
        "is_visible",
        "user_source",
        "group_names",
        "role_names",
        "create_time",
        "update_time",
    }
    assert groups == (6,)
    assert group_columns == {"id", "name", "group_type", "group_source", "is_visible", "create_time", "update_time"}


# ---- paging ----------------------------------------------------------------------------------------------------------


def test_v2_paging_reads_past_a_capped_page_and_dedupes_repeated_rows() -> None:
    policies = _fixture_json("ranger_policies_v2.json")

    def capped(path: str, params: dict) -> str:
        # The server caps pages at 10 rows and repeats the row before each page, as a shifted cursor can.
        if path != _V2_POLICY:
            raise AssertionError(path)
        start = int(params["startIndex"])
        first = max(start - 1, 0)
        return json.dumps(policies[first : start + min(int(params["pageSize"]), 10)])

    client, fake = _client(capped)
    rows = ranger.list_policies(client)

    ids = [row["id"] for row in rows]
    assert len(ids) == 24 and len(set(ids)) == 24
    assert [params["startIndex"] for path, params in fake.requests] == [0, 10, 21, 25]
    assert all(params["pageSize"] == ranger.PAGE_SIZE for _, params in fake.requests)


def test_v2_paging_stops_when_the_server_ignores_the_start_index() -> None:
    policies = _fixture_json("ranger_policies_v2.json")[:5]
    client, fake = _client(lambda path, params: json.dumps(policies))

    assert len(ranger.list_policies(client)) == 5
    assert len(fake.requests) == 2  # the second page adds nothing, so the read ends


def test_v2_services_are_paged_like_policies() -> None:
    """Ranger caps list pages (ranger.db.maxrows.default); services must be read past the first page."""
    services = [{"id": i, "name": f"svc{i}", "type": "hdfs"} for i in range(1, 8)]

    def capped(path: str, params: dict) -> str:
        if path != ranger.V2_SERVICE_PATH:
            raise AssertionError(path)
        start = int(params["startIndex"])
        return json.dumps(services[start : start + 3])

    client, fake = _client(capped)

    assert [row["id"] for row in ranger.list_services(client)] == list(range(1, 8))
    assert len(fake.requests) > 1


def test_xusers_paging_follows_total_count() -> None:
    users = _fixture_json("ranger_xusers_users.json")["vXUsers"]

    def capped(_path: str, params: dict) -> str:
        page = _page(users, params, cap=5)
        return json.dumps({"vXUsers": page, "totalCount": len(users)})

    client, fake = _client(capped)
    rows = ranger.list_users(client)

    assert len(rows) == 12
    assert [params["startIndex"] for _, params in fake.requests] == [0, 5, 10]


# ---- v1 fallback and errors ------------------------------------------------------------------------------------------


def _ranger_v1_only(path: str, params: dict) -> str:
    """A Ranger with no v2 API: v2 answers 404, and the v1 calls answer from the 2.9.0 capture."""
    if path.startswith("/service/public/v2/"):
        raise _not_found(path)
    if path == _V1_REPOSITORY:
        return _fixture("ranger_repository_v1.json")
    if path == _V1_POLICY:
        return _fixture("ranger_policy_v1.json")
    return _ranger_2_9(path, params)


def test_services_fall_back_to_v1_with_a_warning(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level(logging.WARNING)
    with _extract("ranger_services", endpoints.ranger_services, _ranger_v1_only, tmp_path) as conn:
        rows = conn.execute(
            "SELECT name, service_type, is_enabled, create_time FROM ranger_services ORDER BY id"
        ).fetchall()
        configs = conn.execute("SELECT configs FROM ranger_services WHERE name = 'sim_hadoop'").fetchone()[0]

    assert [row[:3] for row in rows] == [("sim_hadoop", "hdfs", True), ("sim_hive", "hive", True)]
    assert rows[0][3] is not None  # v1 gives ISO timestamps, which are converted to the v2 epoch form
    assert json.loads(configs)["fs.default.name"] == "hdfs://ranger-sim-nn:8020"
    assert "v2" in caplog.text and "under-reports" in caplog.text


def test_policies_fall_back_to_v1_and_translate_permission_maps(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.WARNING)
    with _extract("ranger_policy_items", endpoints.ranger_policies, _ranger_v1_only, tmp_path) as conn:
        items = conn.execute("SELECT COUNT(*) FROM ranger_policy_items").fetchone()
        kinds = conn.execute("SELECT DISTINCT item_kind FROM ranger_policy_items").fetchall()
        root = conn.execute(
            "SELECT users, accesses, delegate_admin FROM ranger_policy_items WHERE policy_name = 'all - path'"
        ).fetchone()
    with _extract("ranger_policies", endpoints.ranger_policies, _ranger_v1_only, tmp_path / "policies") as conn:
        policies = conn.execute("SELECT COUNT(*), COUNT(DISTINCT policy_type) FROM ranger_policies").fetchone()

    # v1 returned 20 of its 24 policies in the capture; the translated policies are the 20 it returned.
    assert policies == (20, 1)
    assert items[0] > 0 and kinds == [("allow",)]
    users, accesses, delegate_admin = root
    assert json.loads(users) == ["hdfs"]
    assert [a["type"] for a in json.loads(accesses)] == ["read", "write", "execute"]
    assert delegate_admin is True  # the v1 "admin" permission is the delegate-admin flag
    assert "v2" in caplog.text


def test_service_defs_have_no_v1_fallback() -> None:
    def missing(path: str, params: dict) -> str:
        raise _not_found(path)

    client, _ = _client(missing)
    with pytest.raises(HadoopHttpError) as raised:
        ranger.list_service_defs(client)
    assert raised.value.status_code == 404


def test_users_need_an_admin_account() -> None:
    def forbidden(path: str, params: dict) -> str:
        raise HadoopHttpError(f"GET {path} returned HTTP 403", status_code=403, body="")

    client, _ = _client(forbidden)
    with pytest.raises(HadoopHttpError, match="403"):
        endpoints.ranger_users(client, _config(), RUN)


def test_other_policy_errors_are_not_treated_as_a_missing_v2_api() -> None:
    def server_error(path: str, params: dict) -> str:
        raise HadoopHttpError(f"GET {path} returned HTTP 500", status_code=500, body="")

    client, fake = _client(server_error)
    with pytest.raises(HadoopHttpError, match="500"):
        ranger.list_policies(client)
    assert fake.paths() == [_V2_POLICY]


# ---- the step runner -----------------------------------------------------------------------------------------------


def test_ranger_steps_are_skipped_when_ranger_is_absent(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text("hadoop:\n  distribution: hdp\n", encoding="utf-8")
    db_path = tmp_path / "extract.db"

    message = extract.run_step(
        step="ranger_policies",
        endpoint="ranger",
        fetch=lambda *_: pytest.fail("no request should be made"),
        sql_dir=_SQL_DIR,
        db_path=str(db_path),
        creds_file=creds,
    )

    assert message == "skipped: not configured (hadoop.ranger is absent)"
    assert not db_path.exists()


def test_run_step_writes_the_policies_table_from_the_configured_ranger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, fake = _client(_ranger_2_9)
    built: list[tuple] = []

    def build(base_url: str, **kwargs: Any) -> RestClient:
        built.append((base_url, kwargs["username"], kwargs["password"]))
        return client

    monkeypatch.setattr(extract, "RestClient", build)
    creds = tmp_path / ".credentials.yml"
    creds.write_text(
        "hadoop:\n  distribution: hdp\n  ranger:\n    url: "
        + _RANGER_URL
        + "\n    username: admin\n    password: pw\n",
        encoding="utf-8",
    )

    message = extract.run_step(
        step="ranger_policies",
        endpoint="ranger",
        fetch=endpoints.ranger_policies,
        sql_dir=_SQL_DIR,
        db_path=str(tmp_path / "extract.db"),
        creds_file=creds,
    )

    assert message == "ranger_policies extracted 24 rows"
    assert built == [(_RANGER_URL, "admin", "pw")]
    assert _V2_POLICY in fake.paths()
