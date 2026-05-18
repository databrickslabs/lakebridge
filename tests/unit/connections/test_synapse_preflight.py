"""Unit tests for the Synapse preflight checks.

These tests mock pyodbc, the Synapse connection helpers, and the Azure SDKs so
we can drive each check through every branch (PASS / FAIL / SKIP / UNKNOWN)
without needing a live Synapse workspace.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.connections.preflight import (
    CheckStatus,
    PreflightRunner,
    RunOptions,
)
from databricks.labs.lakebridge.connections.synapse_preflight import (
    ArtifactsSdkCheck,
    AzureCredentialCheck,
    AzureMonitorCheck,
    CredentialsIntegrityCheck,
    MsiStorageRbacCheck,
    NetworkTlsCheck,
    OdbcDriverCheck,
    PerDbOpenCheck,
    PerPoolOpenCheck,
    ProfilerScopeCheck,
    ServerDefaultDbCheck,
    SqlAuthCheck,
    SqlFirewallCheck,
    _synapse_checks,
)


def _base_config(**overrides: Any) -> dict[str, Any]:
    cfg = {
        "workspace": {
            "name": "demo-ws",
            "dedicated_sql_endpoint": "demo-ws.sql.azuresynapse.net",
            "serverless_sql_endpoint": "demo-ws-ondemand.sql.azuresynapse.net",
            "sql_user": "sqluser",
            "sql_password": "p@ssword",
            "driver": "ODBC Driver 18 for SQL Server",
        },
        "azure_api_access": {"development_endpoint": "https://demo-ws.dev.azuresynapse.net"},
        "jdbc": {"auth_type": "sql_authentication"},
        "profiler": {
            "exclude_dedicated_sql_pools": False,
            "exclude_serverless_sql_pool": False,
        },
    }
    cfg.update(overrides)
    return cfg


def _ctx(raw_config: dict[str, Any], shared: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"raw_config": raw_config, "shared": shared or {}}


# ---------------------------------------------------------------------------
# CredentialsIntegrityCheck
# ---------------------------------------------------------------------------


def test_credentials_integrity_pass():
    result = CredentialsIntegrityCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.PASS


def test_credentials_integrity_missing_top_key():
    cfg = _base_config()
    cfg.pop("jdbc")
    result = CredentialsIntegrityCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "jdbc" in result.detail


def test_credentials_integrity_missing_workspace_field():
    cfg = _base_config()
    cfg["workspace"].pop("sql_user")
    result = CredentialsIntegrityCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "sql_user" in result.detail


def test_credentials_integrity_whitespace_in_password():
    cfg = _base_config()
    cfg["workspace"]["sql_password"] = " has-leading-space"
    result = CredentialsIntegrityCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "sql_password" in result.detail


def test_credentials_integrity_non_dict_root():
    result = CredentialsIntegrityCheck().run({"raw_config": "not a dict", "shared": {}}, RunOptions())
    assert result.status == CheckStatus.FAIL


# ---------------------------------------------------------------------------
# ProfilerScopeCheck
# ---------------------------------------------------------------------------


def test_profiler_scope_pass_default():
    result = ProfilerScopeCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.PASS


def test_profiler_scope_fail_when_both_excluded():
    cfg = _base_config()
    cfg["profiler"]["exclude_dedicated_sql_pools"] = True
    cfg["profiler"]["exclude_serverless_sql_pool"] = True
    result = ProfilerScopeCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "nothing left to profile" in result.detail


# ---------------------------------------------------------------------------
# OdbcDriverCheck
# ---------------------------------------------------------------------------


def test_odbc_driver_present(monkeypatch):
    fake_pyodbc = types.SimpleNamespace(drivers=lambda: ["ODBC Driver 18 for SQL Server"])
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    result = OdbcDriverCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.PASS


def test_odbc_driver_missing(monkeypatch):
    fake_pyodbc = types.SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"])
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    result = OdbcDriverCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "ODBC Driver 18 for SQL Server" in result.detail


def test_odbc_driver_pyodbc_missing(monkeypatch):
    # Simulate ImportError on `import pyodbc` by stubbing __import__.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyodbc":
            raise ImportError("no pyodbc here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = OdbcDriverCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "pyodbc" in result.detail


# ---------------------------------------------------------------------------
# NetworkTlsCheck
# ---------------------------------------------------------------------------


def test_network_tls_pass_records_reachable_hosts():
    ctx = _ctx(_base_config())
    with patch.object(NetworkTlsCheck, "_probe_one", staticmethod(lambda *a, **k: "OK")):
        result = NetworkTlsCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.PASS
    assert "demo-ws.sql.azuresynapse.net" in ctx["shared"]["reachable_hosts"]
    assert "demo-ws-ondemand.sql.azuresynapse.net" in ctx["shared"]["reachable_hosts"]


def test_network_tls_partial_failure():
    ctx = _ctx(_base_config())

    def fake_probe(host, port, timeout_s):
        return "OK" if "ondemand" in host else "DNS fail (No such host)"

    with patch.object(NetworkTlsCheck, "_probe_one", staticmethod(fake_probe)):
        result = NetworkTlsCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "demo-ws.sql.azuresynapse.net" in result.detail


# ---------------------------------------------------------------------------
# SqlAuthCheck
# ---------------------------------------------------------------------------


class _FakeConn:
    """Drop-in replacement for the DatabaseManager context manager."""

    def __init__(self, *, check_returns: bool = True, fetch_rows: Iterable[tuple] = (), raises: Exception | None = None):
        self._check = check_returns
        self._rows = list(fetch_rows)
        self._raises = raises

    def __enter__(self):
        if self._raises is not None:
            raise self._raises
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def check_connection(self):
        return self._check

    def fetch(self, _query: str):
        return types.SimpleNamespace(rows=self._rows)


@contextmanager
def _patch_conn(factory):
    """Patch ``create_synapse_connection`` in the synapse_preflight namespace."""
    with patch(
        "databricks.labs.lakebridge.connections.synapse_preflight.create_synapse_connection",
        side_effect=factory,
    ) as m:
        yield m


def test_sql_auth_pass_both_endpoints():
    ctx = _ctx(_base_config(), shared={"reachable_hosts": set()})
    with _patch_conn(lambda **kw: _FakeConn(check_returns=True)):
        result = SqlAuthCheck().run(ctx, RunOptions(short_circuit_dependencies=False))
    assert result.status == CheckStatus.PASS
    assert ctx["shared"]["sql_auth_success"] == {"dedicated": True, "serverless": True}


def test_sql_auth_fail_on_serverless_lake_db_4060():
    ctx = _ctx(_base_config())

    def factory(**kw):
        if kw["endpoint_key"] == "serverless_sql_endpoint":
            raise RuntimeError("4060: Cannot open database 'default'")
        return _FakeConn()

    with _patch_conn(factory):
        result = SqlAuthCheck().run(ctx, RunOptions(short_circuit_dependencies=False))
    assert result.status == CheckStatus.FAIL
    assert "4060" in result.detail


def test_sql_auth_skip_when_both_excluded():
    cfg = _base_config()
    cfg["profiler"]["exclude_dedicated_sql_pools"] = True
    cfg["profiler"]["exclude_serverless_sql_pool"] = True
    result = SqlAuthCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.SKIP


def test_sql_auth_skips_unreachable_host():
    ctx = _ctx(_base_config(), shared={"reachable_hosts": {"demo-ws.sql.azuresynapse.net"}})
    with _patch_conn(lambda **kw: _FakeConn()):
        result = SqlAuthCheck().run(ctx, RunOptions(short_circuit_dependencies=True))
    assert result.status == CheckStatus.FAIL
    assert "unreachable" in result.detail


# ---------------------------------------------------------------------------
# ServerDefaultDbCheck
# ---------------------------------------------------------------------------


def test_server_default_db_pass():
    ctx = _ctx(_base_config())
    with _patch_conn(lambda **kw: _FakeConn(fetch_rows=[("master",)])):
        result = ServerDefaultDbCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.PASS
    assert "master" in result.detail


def test_server_default_db_fail_on_default_db():
    ctx = _ctx(_base_config())
    with _patch_conn(lambda **kw: _FakeConn(fetch_rows=[("default",)])):
        result = ServerDefaultDbCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "ALTER LOGIN" in result.remediation


def test_server_default_db_unknown_when_login_missing():
    ctx = _ctx(_base_config())
    with _patch_conn(lambda **kw: _FakeConn(fetch_rows=[])):
        result = ServerDefaultDbCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.UNKNOWN


# ---------------------------------------------------------------------------
# PerPoolOpenCheck
# ---------------------------------------------------------------------------


def test_per_pool_open_skipped_when_excluded():
    cfg = _base_config()
    cfg["profiler"]["exclude_dedicated_sql_pools"] = True
    result = PerPoolOpenCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.SKIP


def test_per_pool_open_uses_artifacts_discovered_pools():
    ctx = _ctx(_base_config(), shared={"sql_pools": ["pool_a", "pool_b"]})
    seen: list[str] = []

    def factory(**kw):
        seen.append(kw["database"])
        return _FakeConn(check_returns=True)

    with _patch_conn(factory):
        result = PerPoolOpenCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.PASS
    assert set(seen) == {"pool_a", "pool_b"}


def test_per_pool_open_failure_collected():
    ctx = _ctx(_base_config(), shared={"sql_pools": ["pool_a"]})

    def factory(**kw):
        raise RuntimeError("18456: Login failed")

    with _patch_conn(factory):
        result = PerPoolOpenCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "18456" in result.detail


# ---------------------------------------------------------------------------
# PerDbOpenCheck
# ---------------------------------------------------------------------------


def test_per_db_open_skipped_when_excluded():
    cfg = _base_config()
    cfg["profiler"]["exclude_serverless_sql_pool"] = True
    result = PerDbOpenCheck().run(_ctx(cfg), RunOptions())
    assert result.status == CheckStatus.SKIP


def test_per_db_open_samples_in_fast_mode():
    """100 mock DBs but fast mode samples 10."""
    ctx = _ctx(_base_config())
    db_names = [f"db_{i}" for i in range(100)]
    probed: list[str] = []

    def factory(**kw):
        if kw["database"] == "master":
            return _FakeConn(fetch_rows=[(n,) for n in db_names])
        probed.append(kw["database"])
        return _FakeConn(check_returns=True)

    with _patch_conn(factory):
        result = PerDbOpenCheck().run(ctx, RunOptions(serverless_db_sample_size=10))
    assert result.status == CheckStatus.PASS
    assert len(probed) == 10


def test_per_db_open_thorough_probes_all():
    ctx = _ctx(_base_config())
    db_names = [f"db_{i}" for i in range(20)]
    probed: list[str] = []

    def factory(**kw):
        if kw["database"] == "master":
            return _FakeConn(fetch_rows=[(n,) for n in db_names])
        probed.append(kw["database"])
        return _FakeConn(check_returns=True)

    with _patch_conn(factory):
        result = PerDbOpenCheck().run(ctx, RunOptions(thorough=True))
    assert result.status == CheckStatus.PASS
    assert len(probed) == 20


def test_per_db_open_lake_db_failure_has_storage_remediation():
    ctx = _ctx(_base_config())

    def factory(**kw):
        if kw["database"] == "master":
            return _FakeConn(fetch_rows=[("default",), ("ok_db",)])
        if kw["database"] == "default":
            raise RuntimeError("4060: Cannot open database 'default' requested by the login")
        return _FakeConn(check_returns=True)

    with _patch_conn(factory):
        result = PerDbOpenCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "default" in result.detail
    assert "Storage Blob Data Contributor" in result.remediation


def test_per_db_open_non_lake_db_failure_has_orphan_remediation():
    ctx = _ctx(_base_config())

    def factory(**kw):
        if kw["database"] == "master":
            return _FakeConn(fetch_rows=[("normal_db",)])
        raise RuntimeError("916: server principal cannot access database")

    with _patch_conn(factory):
        result = PerDbOpenCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "phantom" in result.remediation or "orphaned" in result.remediation


# ---------------------------------------------------------------------------
# AzureCredentialCheck (relies on lazy import)
# ---------------------------------------------------------------------------


def _install_fake_azure_identity(monkeypatch, *, fail: bool = False):
    fake_mod = types.ModuleType("azure.identity")
    fake_core_exc = types.ModuleType("azure.core.exceptions")

    class _AuthErr(Exception):
        pass

    fake_core_exc.ClientAuthenticationError = _AuthErr

    class _Cred:
        def get_token(self, *_a, **_k):
            if fail:
                raise _AuthErr("not logged in")
            return types.SimpleNamespace(expires_on=0)

    fake_mod.DefaultAzureCredential = _Cred

    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", fake_mod)
    monkeypatch.setitem(sys.modules, "azure.core", types.ModuleType("azure.core"))
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", fake_core_exc)


def test_azure_credential_unknown_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", None)
    result = AzureCredentialCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.UNKNOWN


def test_azure_credential_pass(monkeypatch):
    _install_fake_azure_identity(monkeypatch, fail=False)
    ctx = _ctx(_base_config())
    result = AzureCredentialCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.PASS
    assert "azure_credential" in ctx["shared"]


def test_azure_credential_fail(monkeypatch):
    _install_fake_azure_identity(monkeypatch, fail=True)
    result = AzureCredentialCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.FAIL
    assert "az login" in result.remediation


# ---------------------------------------------------------------------------
# ArtifactsSdkCheck, AzureMonitorCheck, MsiStorageRbacCheck, SqlFirewallCheck
# ---------------------------------------------------------------------------


def test_artifacts_sdk_unknown_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.synapse.artifacts", None)
    result = ArtifactsSdkCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.UNKNOWN


def test_artifacts_sdk_pass(monkeypatch):
    fake_mod = types.ModuleType("azure.synapse.artifacts")
    sql_pool = MagicMock()
    sql_pool.as_dict.return_value = {"name": "pool_a"}
    workspace_get_result = MagicMock()
    workspace_get_result.as_dict.return_value = {"id": "/subscriptions/.../workspaces/ws"}

    class _Client:
        def __init__(self, **_):
            self.sql_pools = types.SimpleNamespace(list=lambda: types.SimpleNamespace(value=[sql_pool]))
            self.workspace = types.SimpleNamespace(get=lambda: workspace_get_result)

    fake_mod.ArtifactsClient = _Client
    monkeypatch.setitem(sys.modules, "azure.synapse", types.ModuleType("azure.synapse"))
    monkeypatch.setitem(sys.modules, "azure.synapse.artifacts", fake_mod)

    ctx = _ctx(_base_config(), shared={"azure_credential": object()})
    result = ArtifactsSdkCheck().run(ctx, RunOptions())
    assert result.status == CheckStatus.PASS
    assert ctx["shared"]["sql_pools"] == ["pool_a"]
    assert ctx["shared"]["workspace_resource_id"] == "/subscriptions/.../workspaces/ws"


def test_artifacts_sdk_skip_without_credential(monkeypatch):
    fake_mod = types.ModuleType("azure.synapse.artifacts")
    fake_mod.ArtifactsClient = lambda **_: None
    monkeypatch.setitem(sys.modules, "azure.synapse", types.ModuleType("azure.synapse"))
    monkeypatch.setitem(sys.modules, "azure.synapse.artifacts", fake_mod)
    result = ArtifactsSdkCheck().run(_ctx(_base_config(), shared={}), RunOptions())
    assert result.status == CheckStatus.SKIP


def test_azure_monitor_unknown_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.monitor.query", None)
    result = AzureMonitorCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.UNKNOWN


def test_msi_storage_rbac_unknown_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.mgmt.authorization", None)
    result = MsiStorageRbacCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.UNKNOWN


def test_sql_firewall_unknown_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.mgmt.synapse", None)
    result = SqlFirewallCheck().run(_ctx(_base_config()), RunOptions())
    assert result.status == CheckStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Suite factory + registry behavior
# ---------------------------------------------------------------------------


def test_synapse_suite_has_expected_dependencies():
    checks = _synapse_checks({"raw_config": _base_config()})
    by_name = {c.name: c for c in checks}
    assert by_name["odbc_driver"].depends_on == ["credentials_integrity"]
    assert "sql_auth" in by_name and "network_tls" in by_name["sql_auth"].depends_on
    assert "per_db_open" in by_name and "sql_auth" in by_name["per_db_open"].depends_on
    assert "artifacts_sdk" in by_name["msi_storage_rbac"].depends_on
    assert {"profiler_scope"}.issubset(set(by_name))


def test_synapse_runner_short_circuits_when_credentials_invalid():
    """The full Synapse suite, end-to-end, with broken credentials -> downstream SKIPs."""
    from databricks.labs.lakebridge.connections import preflight, synapse_preflight  # noqa: F401

    runner = PreflightRunner()
    synapse_preflight.register(runner)
    cfg = _base_config()
    cfg["workspace"].pop("sql_user")  # break credentials

    report = runner.run("synapse", cfg, RunOptions())
    by_name = {r.name: r for r in report.results}
    assert by_name["credentials_integrity"].status == CheckStatus.FAIL
    # Downstream checks should be SKIP because they depend (transitively) on credentials.
    assert by_name["odbc_driver"].status == CheckStatus.SKIP


@pytest.fixture(autouse=True)
def _scrub_azure_module_cache():
    """Some tests inject fake azure submodules; remove them between tests."""
    yield
    for k in list(sys.modules):
        if k.startswith("azure"):
            sys.modules.pop(k, None)
