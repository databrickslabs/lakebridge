from collections.abc import Mapping
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from databricks.labs.lakebridge.resources.assessments.hadoop import rest_client
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    READ_TIMEOUT_SECONDS,
    HadoopApiError,
    HadoopHttpError,
    RestClient,
    find_active_resource_manager,
    normalize_base_url,
)


def _response(status: int = 200, body: str = '{"ok": true}') -> SimpleNamespace:
    # The client reads only the status code and the text, so a stand-in response is enough.
    return SimpleNamespace(status_code=status, text=body)


def _session(*results: object) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = list(results)
    return session


def test_normalize_base_url_keeps_gateway_path_and_strips_trailing_slash() -> None:
    assert (
        normalize_base_url(" https://cluster.azurehdinsight.net/yarnui/ ")
        == "https://cluster.azurehdinsight.net/yarnui"
    )
    assert normalize_base_url("http://rm.example.com:8088") == "http://rm.example.com:8088"


@pytest.mark.parametrize(
    "url",
    [
        "rm.example.com:8088",
        "ftp://rm.example.com",
        "https://user:secret@rm.example.com:8090",
        "https://rm.example.com:8090/ws?x=1",
    ],
)
def test_normalize_base_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid Hadoop service URL"):
        normalize_base_url(url)


def test_get_text_sends_timeouts_and_params_and_returns_body() -> None:
    session = _session(_response(body='{"apps": null}'))
    client = RestClient("http://rm:8088", session=session)

    body = client.get_text("/ws/v1/cluster/apps", {"finishedTimeBegin": 1})

    assert body == '{"apps": null}'
    session.get.assert_called_once_with(
        "http://rm:8088/ws/v1/cluster/apps",
        params={"finishedTimeBegin": 1},
        timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    )


def test_session_gets_basic_auth_and_verify_setting() -> None:
    session = MagicMock(spec=requests.Session)
    RestClient(
        "https://c.example.net/yarnui", username="profiler", password="pw", verify="/etc/ca.pem", session=session
    )

    assert session.auth == ("profiler", "pw")
    assert session.verify == "/etc/ca.pem"


def test_http_error_is_raised_with_status_and_hint() -> None:
    client = RestClient("http://rm:8088", session=_session(_response(status=401, body="denied")))

    with pytest.raises(HadoopApiError, match=r"HTTP 401 \(check the username and password\)"):
        client.get_text("/ws/v1/cluster/info")


def test_non_json_body_is_raised_with_a_clear_message() -> None:
    html = "<html><body>Please sign in</body></html>"
    client = RestClient("https://c.example.net", session=_session(_response(body=html)))

    with pytest.raises(HadoopApiError, match="did not return JSON"):
        client.get_text("/ws/v1/cluster/info")


@pytest.mark.parametrize("body", ["", "   \n", "Unauthorized", "v12"])
def test_an_empty_or_plain_text_body_is_not_json(body: str) -> None:
    client = RestClient("https://c.example.net", session=_session(_response(body=body)))

    with pytest.raises(HadoopApiError, match="did not return JSON"):
        client.get_text("/ws/v1/cluster/apps")


def test_get_text_does_not_parse_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A large application dump is parsed once, by DuckDB when it is stored, not also here."""

    def no_parse(*_args, **_kwargs):
        raise AssertionError("get_text must not parse the body")

    monkeypatch.setattr(rest_client.json, "loads", no_parse)
    client = RestClient("https://c.example.net", session=_session(_response(body=' {"apps": {"app": []}}')))

    assert client.get_text("/ws/v1/cluster/apps") == ' {"apps": {"app": []}}'


def test_get_json_reports_malformed_json_with_the_same_clear_message() -> None:
    client = RestClient("https://c.example.net", session=_session(_response(body='{"apps": [1, 2')))

    with pytest.raises(HadoopApiError, match=r"GET /ws/v1/cluster/apps from https://c.example.net did not return JSON"):
        client.get_json("/ws/v1/cluster/apps")


def test_parse_json_reports_malformed_json_with_the_same_clear_message() -> None:
    with pytest.raises(HadoopApiError, match=r"GET /x from https://c.example.net did not return JSON"):
        rest_client.parse_json("[1, 2", "/x", "https://c.example.net")


def test_connection_error_is_retried_then_succeeds() -> None:
    sleep = MagicMock()
    session = _session(requests.ConnectionError("refused"), _response())
    client = RestClient("http://rm:8088", session=session, sleep=sleep)

    assert client.get_text("/ws/v1/cluster/info") == '{"ok": true}'
    assert session.get.call_count == 2
    sleep.assert_called_once()


def test_connection_error_gives_up_after_the_last_attempt() -> None:
    sleep = MagicMock()
    session = _session(*[requests.ConnectionError("refused")] * MAX_ATTEMPTS)
    client = RestClient("http://rm:8088", session=session, sleep=sleep)

    with pytest.raises(HadoopApiError, match=f"after {MAX_ATTEMPTS} attempts"):
        client.get_text("/ws/v1/cluster/info")
    assert session.get.call_count == MAX_ATTEMPTS
    assert sleep.call_count == MAX_ATTEMPTS - 1


def test_read_timeout_is_not_retried() -> None:
    session = _session(requests.ReadTimeout("slow"))
    client = RestClient("http://rm:8088", session=session, sleep=MagicMock())

    with pytest.raises(HadoopApiError, match="timed out"):
        client.get_text("/ws/v1/cluster/apps")
    assert session.get.call_count == 1


def _fake_session_get(routes: Mapping[str, object], requested: list[str]):
    def get(_self, url, **_kwargs):  # the signature requests.Session.get is called with
        requested.append(url)
        outcome = routes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return get


def test_find_active_resource_manager_skips_standby_and_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []
    routes = {
        "https://rm1.example.net/yarnui/ws/v1/cluster/info": requests.ConnectionError("refused"),
        "https://rm2.example.net/yarnui/ws/v1/cluster/info": _response(body='{"clusterInfo": {"haState": "STANDBY"}}'),
        "https://rm3.example.net/yarnui/ws/v1/cluster/info": _response(
            body='{"clusterInfo": {"haState": "ACTIVE", "hadoopVersion": "3.4.1"}}'
        ),
    }
    monkeypatch.setattr(requests.Session, "get", _fake_session_get(routes, requested))
    monkeypatch.setattr(rest_client.time, "sleep", lambda _seconds: None)

    client = find_active_resource_manager(
        [
            "https://rm1.example.net/yarnui",
            "https://rm2.example.net/yarnui",
            "https://rm3.example.net/yarnui",
        ],
        username="profiler",
        password="pw",
    )

    assert client.base_url == "https://rm3.example.net/yarnui"
    assert requested[-1] == "https://rm3.example.net/yarnui/ws/v1/cluster/info"


def test_find_active_resource_manager_accepts_non_ha_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {"http://rm:8088/ws/v1/cluster/info": _response(body='{"clusterInfo": {"haState": "ACTIVE"}}')}
    monkeypatch.setattr(requests.Session, "get", _fake_session_get(routes, []))

    assert find_active_resource_manager(["http://rm:8088"]).base_url == "http://rm:8088"


def test_find_active_resource_manager_reports_every_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        "http://rm1:8088/ws/v1/cluster/info": _response(body='{"clusterInfo": {"haState": "STANDBY"}}'),
        "http://rm2:8088/ws/v1/cluster/info": requests.ReadTimeout("slow"),
    }
    monkeypatch.setattr(requests.Session, "get", _fake_session_get(routes, []))

    with pytest.raises(HadoopApiError, match="No ACTIVE ResourceManager found") as error:
        find_active_resource_manager(["http://rm1:8088", "http://rm2:8088"])
    assert "haState 'STANDBY'" in str(error.value)
    assert "timed out" in str(error.value)


def test_plain_text_endpoint_is_returned_without_a_json_check() -> None:
    """Cloudera Manager answers GET /api/version with plain text such as v12."""
    client = RestClient("https://cm.example.net:7183", session=_session(_response(body="v12\n")))

    assert client.get_plain_text("/api/version") == "v12\n"


def test_http_error_keeps_its_status_and_body_for_callers_that_handle_it() -> None:
    body = '{"message": "Invalid metric \'impala_memory_rss\' in \'select ...\'"}'
    client = RestClient("https://cm.example.net:7183", session=_session(_response(status=400, body=body)))

    with pytest.raises(HadoopHttpError) as error:
        client.get_text("/api/v12/timeseries")

    assert error.value.status_code == 400
    assert error.value.body == body
    assert isinstance(error.value, HadoopApiError)
