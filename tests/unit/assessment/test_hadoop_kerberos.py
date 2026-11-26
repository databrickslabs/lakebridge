"""The Kerberos (SPNEGO) transport: curl and kinit/klist command lines, status handling, retries and HA discovery.

The tools are faked: ``shutil.which`` finds them and ``subprocess.run`` scripts each result (curl writes ``-o``).
"""

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from databricks.labs.lakebridge.resources.assessments.hadoop import kerberos, rest_client
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import RestEndpoint
from databricks.labs.lakebridge.resources.assessments.hadoop.kerberos import (
    KerberosError,
    KerberosRestClient,
    KerberosSettings,
    curl_argv,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    MAX_ATTEMPTS,
    HadoopApiError,
    HadoopHttpError,
    RestClient,
    find_active_resource_manager,
)

_SPNEGO_CURL_V = "curl 8.7.1\nFeatures: alt-svc AsynchDNS GSS-API HTTP2 HTTPS IPv6 Kerberos SPNEGO UnixSockets"
_RM = "http://rm.example.net:8088"
_INFO_PATH = "/ws/v1/cluster/info"


class FakeSystem:
    """Scripted stand-ins for ``subprocess.run`` and ``shutil.which``, recording every command."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.envs: list[Mapping[str, str]] = []
        # Each curl request consumes one outcome: (exit code, HTTP status text, body) or an exception to raise.
        self.curl_outcomes: list[Any] = []
        self.kinit_exit = 0
        self.kinit_stderr = ""
        self.klist_exit = 0
        self.klist_listing = ""  # what a plain "klist" prints, such as "Default principal: profiler@HADOOP.SIM"
        self.curl_version = _SPNEGO_CURL_V
        self.missing: set[str] = set()

    def which(self, name: str) -> str | None:
        return None if name in self.missing else f"/usr/bin/{name}"

    def run(self, argv, *, env, capture_output, text, timeout, check):
        assert capture_output and text and not check and timeout > 0  # the call shape the transport relies on
        self.commands.append(list(argv))
        self.envs.append(env)
        tool = Path(argv[0]).name
        if tool == "curl" and argv[1] == "-V":
            return subprocess.CompletedProcess(argv, 0, self.curl_version, "")
        if tool == "curl":
            outcome = self.curl_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            exit_code, status, body = outcome
            if exit_code == 0:
                Path(argv[argv.index("-o") + 1]).write_text(body, encoding="utf-8")
            return subprocess.CompletedProcess(argv, exit_code, status, "" if exit_code == 0 else "curl: (7) refused")
        if tool == "kinit":
            return subprocess.CompletedProcess(argv, self.kinit_exit, "", self.kinit_stderr)
        if tool == "klist":
            listing = self.klist_listing if argv[1:] == [] else ""
            return subprocess.CompletedProcess(argv, self.klist_exit, listing, "")
        raise AssertionError(f"unexpected command {argv}")

    def curl_requests(self) -> list[list[str]]:
        return [argv for argv in self.commands if Path(argv[0]).name == "curl" and argv[1] != "-V"]

    def tool_calls(self, tool: str) -> list[list[str]]:
        return [argv for argv in self.commands if Path(argv[0]).name == tool]


@pytest.fixture
def system(monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    fake = FakeSystem()
    monkeypatch.setattr(kerberos.shutil, "which", fake.which)
    monkeypatch.setattr(kerberos.subprocess, "run", fake.run)
    kerberos.prepare.cache_clear()
    return fake


@pytest.fixture
def transport(system: FakeSystem, monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    """The request path only: the curl and ticket checks run once per process, so they are skipped here."""
    monkeypatch.setattr(kerberos, "prepare", lambda _settings: None)
    return system


def _client(sleep: MagicMock | None = None, **settings: str | None) -> KerberosRestClient:
    return KerberosRestClient(_RM, kerberos=KerberosSettings(**settings), sleep=sleep or MagicMock())


def test_curl_runs_with_negotiate_and_an_empty_user_and_no_shell(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "200", '{"clusterInfo": {}}')]

    _client().get_json(_INFO_PATH)

    (argv,) = transport.curl_requests()
    assert argv[1:] == [
        "-sS",
        "--negotiate",
        "-u",
        ":",
        "--connect-timeout",
        "10",
        "--max-time",
        "300",
        "-H",
        "Accept: application/json",
        "-o",
        argv[argv.index("-o") + 1],
        "-w",
        "%{http_code}",
        f"{_RM}{_INFO_PATH}",
    ]
    # Quiet mode is not used, so the user's .curlrc (and CURL_HOME) still apply, as in the original profiler.
    assert "-q" not in argv and "--disable" not in argv and "-f" not in argv


def test_tls_flags_follow_verify_ssl() -> None:
    assert curl_argv("curl", "https://rm", verify=True, body_path="/tmp/b") == [
        "curl",
        "-sS",
        "--negotiate",
        "-u",
        ":",
        "--connect-timeout",
        "10",
        "--max-time",
        "300",
        "-H",
        "Accept: application/json",
        "-o",
        "/tmp/b",
        "-w",
        "%{http_code}",
        "https://rm",
    ]
    assert curl_argv("curl", "https://rm", verify=False, body_path="/tmp/b")[-2:] == ["-k", "https://rm"]
    assert curl_argv("curl", "https://rm", verify="/etc/ca.pem", body_path="/tmp/b")[-3:] == [
        "--cacert",
        "/etc/ca.pem",
        "https://rm",
    ]


def test_params_are_url_encoded_and_keep_the_query_a_path_already_has(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "200", "[]"), (0, "200", "[]")]
    client = _client()

    client.get_json("/api/v1/applications", {"minDate": "2026-01-01", "x": "a b&c"})
    client.get_json("/queries?json", {"query": "a b"})

    first, second = (argv[-1] for argv in transport.curl_requests())
    assert first == f"{_RM}/api/v1/applications?minDate=2026-01-01&x=a+b%26c"
    assert second == f"{_RM}/queries?json&query=a+b"


def test_200_json_is_returned_with_a_clean_environment_for_the_tools(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "200", '{"clusterInfo": {"haState": "ACTIVE"}}')]

    body = _client().get_json(_INFO_PATH)

    assert body == {"clusterInfo": {"haState": "ACTIVE"}}


def test_401_is_an_error_with_the_kerberos_message_not_a_success(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "401", "<html>Error 401 Authentication required</html>")]

    with pytest.raises(HadoopHttpError) as error:
        _client().get_text(_INFO_PATH)

    assert error.value.status_code == 401
    message = str(error.value)
    assert "Kerberos authentication failed (HTTP 401)" in message
    assert "no valid ticket" in message
    assert "HTTP/rm.example.net" in message
    assert "not an IP address or an alias" in message


def test_non_json_200_is_reported_like_the_requests_transport(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "200", "<html>Sign in</html>")]

    with pytest.raises(HadoopApiError, match="did not return JSON"):
        _client().get_text(_INFO_PATH)


def test_connection_failure_is_retried_then_succeeds(transport: FakeSystem) -> None:
    sleep = MagicMock()
    transport.curl_outcomes = [(7, "", ""), (0, "200", '{"ok": true}')]

    assert _client(sleep=sleep).get_text(_INFO_PATH) == '{"ok": true}'
    assert len(transport.curl_requests()) == 2
    sleep.assert_called_once()


def test_connection_failure_gives_up_after_the_last_attempt(transport: FakeSystem) -> None:
    sleep = MagicMock()
    transport.curl_outcomes = [(6, "", "")] * MAX_ATTEMPTS

    with pytest.raises(HadoopApiError, match=f"after {MAX_ATTEMPTS} attempts"):
        _client(sleep=sleep).get_text(_INFO_PATH)
    assert len(transport.curl_requests()) == MAX_ATTEMPTS
    assert sleep.call_count == MAX_ATTEMPTS - 1


def test_timeout_is_reported_and_not_retried(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(28, "000", "")]

    with pytest.raises(HadoopApiError, match="timed out"):
        _client(sleep=MagicMock()).get_text(_INFO_PATH)
    assert len(transport.curl_requests()) == 1


def test_a_subprocess_timeout_is_reported_as_a_timeout(transport: FakeSystem) -> None:
    transport.curl_outcomes = [subprocess.TimeoutExpired(cmd="curl", timeout=1)]

    with pytest.raises(HadoopApiError, match="timed out"):
        _client().get_text(_INFO_PATH)


def test_other_curl_failures_are_reported_with_their_exit_code_and_not_retried(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(35, "000", "")]

    with pytest.raises(HadoopApiError, match="curl exit 35"):
        _client(sleep=MagicMock()).get_text(_INFO_PATH)
    assert len(transport.curl_requests()) == 1


def test_ccache_and_krb5_config_reach_curl_through_the_environment(transport: FakeSystem) -> None:
    transport.curl_outcomes = [(0, "200", "{}")]

    _client(ccache="FILE:/tmp/krb5cc_profiler", krb5_config="/etc/krb5-test.conf").get_text(_INFO_PATH)

    env = transport.envs[-1]
    assert env["KRB5CCNAME"] == "FILE:/tmp/krb5cc_profiler"
    assert env["KRB5_CONFIG"] == "/etc/krb5-test.conf"


def test_settings_environment_leaves_unset_variables_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRB5CCNAME", "FILE:/tmp/from-the-shell")
    monkeypatch.delenv("KRB5_CONFIG", raising=False)

    env = KerberosSettings().environment()

    assert env["KRB5CCNAME"] == "FILE:/tmp/from-the-shell"
    assert "KRB5_CONFIG" not in env


# ---- ticket and tool checks (the real prepare) -------------------------------------------------------------------------


@pytest.fixture
def real_prepare(system: FakeSystem) -> FakeSystem:
    """The real curl and ticket checks, run against the fake tools (``system`` clears the cache)."""
    return system


def test_keytab_runs_kinit_with_an_argument_list_when_there_is_no_valid_ticket(
    system: FakeSystem, real_prepare, tmp_path: Path
) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    system.klist_exit = 1  # no valid ticket in the cache
    system.curl_outcomes = [(0, "200", "{}")]

    _client(keytab=str(keytab), principal="profiler@HADOOP.SIM").get_text(_INFO_PATH)

    assert system.tool_calls("kinit") == [["/usr/bin/kinit", "-kt", str(keytab), "profiler@HADOOP.SIM"]]
    assert system.tool_calls("klist") == [["/usr/bin/klist", "-s"]]


@pytest.mark.parametrize(
    "listing",
    [
        "Ticket cache: FILE:/tmp/x\nDefault principal: profiler@HADOOP.SIM\n",  # MIT krb5
        "Credentials cache: FILE:/tmp/x\n        Principal: profiler@HADOOP.SIM\n",  # Heimdal (macOS)
    ],
)
def test_keytab_reuses_a_valid_ticket_for_the_same_principal(
    system: FakeSystem, real_prepare, tmp_path: Path, listing: str
) -> None:
    """Each pipeline step is its own process: a run's steps share one ticket instead of each asking the KDC."""
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    system.klist_listing = listing
    system.curl_outcomes = [(0, "200", "{}")]

    _client(keytab=str(keytab), principal="profiler@HADOOP.SIM").get_text(_INFO_PATH)

    assert system.tool_calls("kinit") == []


def test_keytab_runs_kinit_when_the_valid_ticket_is_another_principal(
    system: FakeSystem, real_prepare, tmp_path: Path
) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    system.klist_listing = "Default principal: someone.else@HADOOP.SIM\n"
    system.curl_outcomes = [(0, "200", "{}")]

    _client(keytab=str(keytab), principal="profiler@HADOOP.SIM").get_text(_INFO_PATH)

    assert len(system.tool_calls("kinit")) == 1


def test_keytab_without_a_ccache_uses_the_profiler_s_own_cache_not_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KRB5CCNAME", "FILE:/tmp/krb5cc_the_operator")
    monkeypatch.setattr(kerberos.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(kerberos.getpass, "getuser", lambda: "etl")

    env = KerberosSettings(keytab="/k/profiler.keytab", principal="profiler@HADOOP.SIM").environment()

    assert env["KRB5CCNAME"] == f"FILE:{tmp_path}/lakebridge-hadoop-krb5cc-etl-profiler_HADOOP.SIM"


def test_a_configured_ccache_is_used_as_given_with_a_keytab(monkeypatch: pytest.MonkeyPatch) -> None:
    env = KerberosSettings(
        keytab="/k/profiler.keytab", principal="profiler@HADOOP.SIM", ccache="FILE:/tmp/krb5cc_profiler"
    ).environment()

    assert env["KRB5CCNAME"] == "FILE:/tmp/krb5cc_profiler"


def test_kinit_failure_is_a_clear_error_with_kinit_s_message(system: FakeSystem, real_prepare, tmp_path: Path) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    system.kinit_exit = 1
    system.kinit_stderr = "kinit: Password incorrect while getting initial credentials\n"

    with pytest.raises(KerberosError, match=r"kinit -kt .* failed \(exit 1\): kinit: Password incorrect"):
        _client(keytab=str(keytab), principal="profiler@HADOOP.SIM")
    assert system.curl_requests() == []


def test_without_a_keytab_an_expired_or_missing_ticket_fails_before_any_request(
    system: FakeSystem, real_prepare
) -> None:
    system.klist_exit = 1

    with pytest.raises(KerberosError, match="No valid Kerberos ticket"):
        _client(principal="profiler@HADOOP.SIM")
    assert system.tool_calls("klist") == [["/usr/bin/klist", "-s"]]
    assert system.curl_requests() == []


def test_without_a_keytab_an_existing_valid_ticket_is_used(system: FakeSystem, real_prepare) -> None:
    system.curl_outcomes = [(0, "200", "{}")]

    _client().get_text(_INFO_PATH)

    assert system.tool_calls("klist") == [["/usr/bin/klist", "-s"]]
    assert system.tool_calls("kinit") == []


def test_curl_without_spnego_is_rejected_before_any_request(system: FakeSystem, real_prepare) -> None:
    system.curl_version = "curl 8.7.1\nFeatures: HTTP2 libz"

    with pytest.raises(KerberosError, match="no Kerberos/SPNEGO support"):
        _client()
    assert system.curl_requests() == []


def test_missing_kinit_is_a_clear_error(system: FakeSystem, real_prepare, tmp_path: Path) -> None:
    keytab = tmp_path / "profiler.keytab"
    keytab.write_text("synthetic", encoding="utf-8")
    system.missing.add("kinit")

    with pytest.raises(KerberosError, match="needs 'kinit' on PATH"):
        _client(keytab=str(keytab), principal="profiler@HADOOP.SIM")


def test_the_ticket_is_prepared_once_per_settings_not_per_client(system: FakeSystem, real_prepare) -> None:
    settings = KerberosSettings(principal="profiler@HADOOP.SIM")
    KerberosRestClient(_RM, kerberos=settings, sleep=MagicMock())
    KerberosRestClient(_RM, kerberos=settings, sleep=MagicMock())

    assert len(system.tool_calls("klist")) == 1


def test_ha_discovery_runs_over_the_kerberos_transport(transport: FakeSystem, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest_client.time, "sleep", lambda _seconds: None)
    transport.curl_outcomes = [
        (7, "", ""),
        (7, "", ""),
        (7, "", ""),  # rm1 is down: three attempts
        (0, "200", '{"clusterInfo": {"haState": "STANDBY"}}'),  # rm2 is standby
        (0, "200", '{"clusterInfo": {"haState": "ACTIVE"}}'),  # rm3 is active
    ]
    endpoint = RestEndpoint(
        urls=("http://rm1:8088", "http://rm2:8088", "http://rm3:8088"),
        kerberos=KerberosSettings(principal="profiler@HADOOP.SIM"),
    )

    client = find_active_resource_manager(endpoint.urls, make_client=endpoint.client)

    assert isinstance(client, KerberosRestClient)
    assert client.base_url == "http://rm3:8088"


def test_endpoint_client_is_a_kerberos_client_only_with_kerberos_settings(transport: FakeSystem) -> None:
    assert isinstance(RestEndpoint(urls=(_RM,)).client(_RM), RestClient)
    assert not isinstance(RestEndpoint(urls=(_RM,)).client(_RM), KerberosRestClient)
    assert isinstance(RestEndpoint(urls=(_RM,), kerberos=KerberosSettings()).client(_RM), KerberosRestClient)


def test_kerberos_error_is_a_hadoop_api_error() -> None:
    assert issubclass(KerberosError, HadoopApiError)
