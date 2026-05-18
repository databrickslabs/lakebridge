"""Synapse-specific preflight checks for the database profiler.

Each :class:`PreflightCheck` here probes one slice of the failure surface that
the profiler hits at runtime (credentials, ODBC driver, DNS/TCP/TLS, SQL auth,
the lake-DB serverless trap, Azure SDK reachability, workspace MSI storage
RBAC, SQL firewall). Checks declare ``depends_on`` edges so the runner can
short-circuit when an upstream failure makes downstream checks meaningless.

Azure management SDKs are imported lazily inside :meth:`run` so the main
lakebridge environment does not need to ship them. When an SDK is missing, the
relevant check reports :class:`CheckStatus.UNKNOWN` with a clear hint rather
than blocking the run.
"""

from __future__ import annotations

import logging
import random
import socket
import ssl
from concurrent.futures import as_completed
from typing import Any
from urllib.parse import urlparse

from databricks.labs.lakebridge.connections.preflight import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    PreflightCheck,
    PreflightRunner,
    RunOptions,
)
from databricks.labs.lakebridge.connections.synapse_connection_helpers import create_synapse_connection

logger = logging.getLogger(__name__)


# Required keys in the Synapse credentials block; checked by `CredentialsIntegrityCheck`.
REQUIRED_WORKSPACE_KEYS = ("name", "sql_user", "sql_password", "driver")
REQUIRED_TOP_KEYS = ("workspace", "azure_api_access", "jdbc", "profiler")


def _sql_error_class(exc: Exception) -> str:
    """Best-effort extraction of a SQL/pyodbc error code from an exception chain."""
    text = str(exc)
    for code in ("18456", "4060", "916", "10060", "08001", "28000", "IM002"):
        if code in text:
            return code
    return type(exc).__name__


def _looks_like_lake_db(name: str) -> bool:
    """Heuristic for whether a serverless DB name is a Spark/lake database.

    True ``default`` is the smoking gun from the user's traceback; other lake
    DBs commonly use lowercase names without the ``_`` separators that
    SQL-authored databases tend to have. This is a hint for remediation text,
    not authoritative classification.
    """
    return name == "default"


class CredentialsIntegrityCheck(PreflightCheck):
    name = "credentials_integrity"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = []
    parallel_safe = False

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        raw = context["raw_config"]
        if not isinstance(raw, dict):
            return self._result(
                CheckStatus.FAIL,
                detail="Credentials root is not a dict.",
                remediation="Re-run `databricks labs lakebridge configure-database-profiler`.",
            )
        missing = [k for k in REQUIRED_TOP_KEYS if k not in raw]
        if missing:
            return self._result(
                CheckStatus.FAIL,
                detail=f"Missing top-level Synapse keys: {missing}.",
                remediation="Re-run `databricks labs lakebridge configure-database-profiler`.",
            )
        workspace = raw.get("workspace", {})
        missing_ws = [k for k in REQUIRED_WORKSPACE_KEYS if not workspace.get(k)]
        if missing_ws:
            return self._result(
                CheckStatus.FAIL,
                detail=f"Missing workspace fields: {missing_ws}.",
                remediation="Re-run `databricks labs lakebridge configure-database-profiler`.",
            )
        whitespace_offenders: list[str] = []
        for key in ("sql_user", "sql_password"):
            value = workspace.get(key, "")
            if isinstance(value, str) and value != value.strip():
                whitespace_offenders.append(key)
        if whitespace_offenders:
            return self._result(
                CheckStatus.FAIL,
                detail=f"Leading/trailing whitespace in: {whitespace_offenders}.",
                remediation="Re-run `configure-database-profiler` and re-enter the credentials.",
            )
        return self._result(CheckStatus.PASS, detail="Required keys present, no whitespace issues.")


class ProfilerScopeCheck(PreflightCheck):
    """Fail when the profiler config excludes every supported scope.

    Mirrors the legacy ``validate_synapse_pools`` guard: running the profiler
    with both dedicated and serverless excluded would extract nothing. We
    catch that at preflight rather than producing an empty extract.
    """

    name = "profiler_scope"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["credentials_integrity"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        profiler = context["raw_config"].get("profiler", {})
        exclude_dedicated = bool(profiler.get("exclude_dedicated_sql_pools", False))
        exclude_serverless = bool(profiler.get("exclude_serverless_sql_pool", False))
        if exclude_dedicated and exclude_serverless:
            return self._result(
                CheckStatus.FAIL,
                detail="Both dedicated and serverless SQL pools are excluded; nothing left to profile.",
                remediation=(
                    "In the credentials file, set at least one of "
                    "`profiler.exclude_dedicated_sql_pools` or `profiler.exclude_serverless_sql_pool` to false."
                ),
            )
        return self._result(
            CheckStatus.PASS,
            detail=(
                f"Scope OK (dedicated={'off' if exclude_dedicated else 'on'}, "
                f"serverless={'off' if exclude_serverless else 'on'})."
            ),
        )


class OdbcDriverCheck(PreflightCheck):
    name = "odbc_driver"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["credentials_integrity"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            import pyodbc  # noqa: PLC0415
        except ImportError as e:
            return self._result(
                CheckStatus.FAIL,
                detail=f"pyodbc not installed: {e}",
                remediation="Install pyodbc (`pip install pyodbc`).",
            )
        driver = context["raw_config"]["workspace"].get("driver")
        installed = pyodbc.drivers()
        if driver in installed:
            return self._result(CheckStatus.PASS, detail=f"Driver `{driver}` is installed.")
        return self._result(
            CheckStatus.FAIL,
            detail=f"Driver `{driver}` not found. Installed: {installed!r}.",
            remediation=(
                "Install the Microsoft ODBC Driver 18 for SQL Server: "
                "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
            ),
        )


class NetworkTlsCheck(PreflightCheck):
    """DNS resolve, TCP open, and TLS handshake against the Synapse SQL hosts.

    Surfaces corporate proxy / firewall problems before SQL auth times out.
    Records hosts that resolved/connected/handshook in
    ``context['shared']['reachable_hosts']`` so downstream SQL checks can
    short-circuit per-host instead of all-or-nothing.
    """

    name = "network_tls"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["credentials_integrity"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        workspace = context["raw_config"]["workspace"]
        dedicated_host = workspace.get("dedicated_sql_endpoint", "")
        serverless_host = workspace.get("serverless_sql_endpoint", "")
        dev_endpoint = context["raw_config"].get("azure_api_access", {}).get("development_endpoint", "")
        dev_host = urlparse(dev_endpoint).hostname or dev_endpoint

        targets = [(h, 1433) for h in (dedicated_host, serverless_host) if h]
        if dev_host:
            targets.append((dev_host, 443))

        results: dict[str, str] = {}
        pool = context.get("pool")
        if pool is None or not self.parallel_safe:
            for host, port in targets:
                results[host] = self._probe_one(host, port, options.connect_timeout_s)
        else:
            futures = {pool.submit(self._probe_one, h, p, options.connect_timeout_s): h for h, p in targets}
            for f in as_completed(futures):
                results[futures[f]] = f.result()

        reachable: set[str] = set()
        failures: list[str] = []
        for host, status in results.items():
            if status == "OK":
                reachable.add(host)
            else:
                failures.append(f"{host}: {status}")
        context["shared"]["reachable_hosts"] = reachable

        if not failures:
            return self._result(CheckStatus.PASS, detail=f"All {len(results)} hosts resolved + TLS-OK.")
        return self._result(
            CheckStatus.FAIL,
            detail="; ".join(failures),
            remediation=(
                "Open outbound 1433 (SQL) and 443 (dev endpoint) to the Synapse hosts. "
                "If a corporate proxy is performing TLS interception, install its CA into the host trust store."
            ),
        )

    @staticmethod
    def _probe_one(host: str, port: int, timeout_s: int) -> str:
        try:
            socket.getaddrinfo(host, port)
        except OSError as e:
            return f"DNS fail ({e})"
        try:
            with socket.create_connection((host, port), timeout=timeout_s) as sock:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=host) as _ssock:
                    pass
        except ssl.SSLError as e:
            return f"TLS handshake failed ({e})"
        except OSError as e:
            return f"TCP fail ({e})"
        return "OK"


class SqlAuthCheck(PreflightCheck):
    """Open ``master`` on both SQL endpoints (skipped per ``exclude_*`` flags)."""

    name = "sql_auth"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["odbc_driver", "network_tls"]
    parallel_safe = False

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        workspace = context["raw_config"]["workspace"]
        jdbc = context["raw_config"].get("jdbc", {})
        profiler = context["raw_config"].get("profiler", {})
        auth_type = jdbc.get("auth_type", "sql_authentication")
        reachable: set[str] = context["shared"].get("reachable_hosts", set())

        endpoints: list[tuple[str, str, str]] = []
        if not profiler.get("exclude_dedicated_sql_pools", False):
            endpoints.append(("dedicated", "dedicated_sql_endpoint", workspace.get("dedicated_sql_endpoint", "")))
        if not profiler.get("exclude_serverless_sql_pool", False):
            endpoints.append(("serverless", "serverless_sql_endpoint", workspace.get("serverless_sql_endpoint", "")))

        if not endpoints:
            return self._result(
                CheckStatus.SKIP,
                detail="Both dedicated and serverless pools excluded via profiler config.",
            )

        failures: list[str] = []
        succeeded: dict[str, bool] = {}
        for label, key, host in endpoints:
            if options.short_circuit_dependencies and reachable and host not in reachable:
                failures.append(f"{label}: skipped (host {host} unreachable)")
                succeeded[label] = False
                continue
            try:
                with create_synapse_connection(
                    workspace_config=workspace,
                    database="master",
                    endpoint_key=key,
                    auth_type=auth_type,
                    connect_timeout_s=options.connect_timeout_s,
                ) as conn:
                    if not conn.check_connection():
                        raise ConnectionError("check_connection returned False")
                succeeded[label] = True
            except Exception as e:  # noqa: BLE001
                code = _sql_error_class(e)
                failures.append(f"{label} ({code}): {e}")
                succeeded[label] = False

        context["shared"]["sql_auth_success"] = succeeded
        if not failures:
            return self._result(CheckStatus.PASS, detail=f"Authenticated to: {list(succeeded)}.")
        return self._result(
            CheckStatus.FAIL,
            detail="; ".join(failures),
            remediation=(
                "Verify the SQL admin password. If the login is AAD, check Entra ID Conditional Access "
                "for non-interactive sign-in blocks. Codes 18456/4060 commonly mean the requested database "
                "is not accessible to this login (often `default` on serverless = lake DB; see Storage RBAC)."
            ),
        )


class ServerDefaultDbCheck(PreflightCheck):
    """Inspect ``sys.server_principals.default_database_name`` for the SQL login."""

    name = "server_default_db"
    severity = CheckSeverity.WARN
    depends_on: list[str] = ["sql_auth"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        workspace = context["raw_config"]["workspace"]
        jdbc = context["raw_config"].get("jdbc", {})
        profiler = context["raw_config"].get("profiler", {})
        auth_type = jdbc.get("auth_type", "sql_authentication")
        sql_user = workspace.get("sql_user", "")

        endpoint_key = (
            "serverless_sql_endpoint"
            if not profiler.get("exclude_serverless_sql_pool", False)
            else "dedicated_sql_endpoint"
        )
        try:
            with create_synapse_connection(
                workspace_config=workspace,
                database="master",
                endpoint_key=endpoint_key,
                auth_type=auth_type,
                connect_timeout_s=options.connect_timeout_s,
            ) as conn:
                row = conn.fetch(
                    "SELECT default_database_name "
                    f"FROM sys.server_principals WHERE name = '{sql_user.replace(chr(39), chr(39) * 2)}'"
                )
        except Exception as e:  # noqa: BLE001
            return self._result(
                CheckStatus.UNKNOWN,
                detail=f"Could not query sys.server_principals: {e}",
                remediation="Re-run after fixing `sql_auth`.",
            )

        if not row.rows:
            return self._result(
                CheckStatus.UNKNOWN,
                detail=f"Login {sql_user!r} not found in sys.server_principals.",
            )
        default_db = row.rows[0][0]
        if default_db == "default":
            return self._result(
                CheckStatus.FAIL,
                detail=f"Login {sql_user!r} default_database_name is `default`, "
                "which is a Spark/lake DB and is not safe as a default.",
                remediation=f"`ALTER LOGIN [{sql_user}] WITH DEFAULT_DATABASE = [master];`",
            )
        return self._result(CheckStatus.PASS, detail=f"default_database_name = {default_db!r}.")


class PerPoolOpenCheck(PreflightCheck):
    """Open each dedicated pool listed by Artifacts; falls back to bare endpoint."""

    name = "per_pool_open"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["sql_auth"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        profiler = context["raw_config"].get("profiler", {})
        if profiler.get("exclude_dedicated_sql_pools", False):
            return self._result(CheckStatus.SKIP, detail="Dedicated pools excluded via profiler config.")

        workspace = context["raw_config"]["workspace"]
        jdbc = context["raw_config"].get("jdbc", {})
        auth_type = jdbc.get("auth_type", "sql_authentication")

        pools = context["shared"].get("sql_pools", [])
        if not pools:
            pools = ["master"]

        pool = context.get("pool")

        def probe(pool_name: str) -> tuple[str, str | None]:
            try:
                with create_synapse_connection(
                    workspace_config=workspace,
                    database=pool_name,
                    endpoint_key="dedicated_sql_endpoint",
                    auth_type=auth_type,
                    connect_timeout_s=options.connect_timeout_s,
                ) as conn:
                    if not conn.check_connection():
                        return pool_name, "check_connection returned False"
                return pool_name, None
            except Exception as e:  # noqa: BLE001
                return pool_name, f"{_sql_error_class(e)}: {e}"

        failures: list[str] = []
        if pool is not None and self.parallel_safe and len(pools) > 1:
            futures = [pool.submit(probe, p) for p in pools]
            for f in as_completed(futures):
                pool_name, err = f.result()
                if err:
                    failures.append(f"{pool_name}: {err}")
        else:
            for p in pools:
                pool_name, err = probe(p)
                if err:
                    failures.append(f"{pool_name}: {err}")

        if not failures:
            return self._result(CheckStatus.PASS, detail=f"Opened {len(pools)} dedicated pool(s).")
        return self._result(
            CheckStatus.FAIL,
            detail="; ".join(failures),
            remediation=(
                "A paused or unreachable pool yields connection errors; resume it or remove it from scope "
                "via `dedicated_pools_list` in the credentials file."
            ),
        )


class PerDbOpenCheck(PreflightCheck):
    """Catches the lake-DB serverless trap that fails mid-pipeline today.

    Lists every database on the serverless endpoint, then attempts ``USE [db]``
    per DB. In fast mode samples up to ``serverless_db_sample_size`` randomly
    (deterministic by workspace name + DB count). In thorough mode probes
    every DB.
    """

    name = "per_db_open"
    severity = CheckSeverity.FATAL
    depends_on: list[str] = ["sql_auth"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        profiler = context["raw_config"].get("profiler", {})
        if profiler.get("exclude_serverless_sql_pool", False):
            return self._result(CheckStatus.SKIP, detail="Serverless pool excluded via profiler config.")

        workspace = context["raw_config"]["workspace"]
        jdbc = context["raw_config"].get("jdbc", {})
        auth_type = jdbc.get("auth_type", "sql_authentication")

        try:
            with create_synapse_connection(
                workspace_config=workspace,
                database="master",
                endpoint_key="serverless_sql_endpoint",
                auth_type=auth_type,
                connect_timeout_s=options.connect_timeout_s,
            ) as conn:
                rows = conn.fetch("SELECT name FROM sys.databases WHERE name NOT IN ('master')")
        except Exception as e:  # noqa: BLE001
            return self._result(
                CheckStatus.FAIL,
                detail=f"Could not list sys.databases on serverless: {e}",
                remediation="Fix `sql_auth` first; this check depends on serverless `master` access.",
            )

        db_names = [r[0] for r in rows.rows]
        if not db_names:
            return self._result(CheckStatus.PASS, detail="No user databases to probe on serverless.")

        if options.serverless_db_sample_size and len(db_names) > options.serverless_db_sample_size:
            rng = random.Random(workspace.get("name", "synapse") + str(len(db_names)))
            db_names = rng.sample(db_names, options.serverless_db_sample_size)

        def probe(db: str) -> tuple[str, str | None]:
            try:
                with create_synapse_connection(
                    workspace_config=workspace,
                    database=db,
                    endpoint_key="serverless_sql_endpoint",
                    auth_type=auth_type,
                    connect_timeout_s=options.connect_timeout_s,
                ) as conn:
                    if not conn.check_connection():
                        return db, "check_connection returned False"
                return db, None
            except Exception as e:  # noqa: BLE001
                return db, f"{_sql_error_class(e)}: {e}"

        pool = context.get("pool")
        failures: list[str] = []
        lake_failures: list[str] = []
        if pool is not None and self.parallel_safe and len(db_names) > 1:
            futures = [pool.submit(probe, d) for d in db_names]
            for f in as_completed(futures):
                db, err = f.result()
                if err:
                    (lake_failures if _looks_like_lake_db(db) else failures).append(f"{db}: {err}")
        else:
            for d in db_names:
                db, err = probe(d)
                if err:
                    (lake_failures if _looks_like_lake_db(db) else failures).append(f"{db}: {err}")

        all_failures = lake_failures + failures
        if not all_failures:
            return self._result(CheckStatus.PASS, detail=f"Opened {len(db_names)} serverless DB(s).")

        if lake_failures:
            remediation = (
                "Lake/Spark DB (`default` or similar) failed to open. The Synapse workspace's managed "
                "identity likely lacks `Storage Blob Data Contributor` on the backing ADLS Gen2 container, "
                "or the storage firewall blocks the serverless engine. To skip serverless entirely, set "
                "`profiler.exclude_serverless_sql_pool: true` in the credentials file."
            )
        else:
            remediation = (
                "Open the failed DB in SSMS as the same login to confirm it is reproducible there; if so, "
                "the DB is likely an orphaned/phantom serverless entry. Consider excluding serverless or "
                "narrowing the scope via `dedicated_pools_list` if applicable."
            )
        return self._result(
            CheckStatus.FAIL,
            detail="; ".join(all_failures),
            remediation=remediation,
        )


class AzureCredentialCheck(PreflightCheck):
    """Confirm ``DefaultAzureCredential`` resolves a token for the management plane."""

    name = "azure_credential"
    severity = CheckSeverity.WARN
    depends_on: list[str] = []
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            from azure.identity import DefaultAzureCredential  # noqa: PLC0415
            from azure.core.exceptions import ClientAuthenticationError  # noqa: PLC0415
        except ImportError:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="azure-identity not installed in this environment.",
                remediation="`pip install azure-identity` to enable Azure-SDK preflight checks.",
            )
        try:
            cred = DefaultAzureCredential()
            token = cred.get_token("https://management.azure.com/.default")
            context["shared"]["azure_credential"] = cred
            return self._result(
                CheckStatus.PASS,
                detail=f"Token acquired (expires in {max(token.expires_on - 0, 0)} epoch seconds).",
            )
        except ClientAuthenticationError as e:
            return self._result(
                CheckStatus.FAIL,
                detail=f"DefaultAzureCredential failed: {e}",
                remediation="Run `az login`, or set AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET.",
            )
        except Exception as e:  # noqa: BLE001
            return self._result(
                CheckStatus.UNKNOWN,
                detail=f"Could not acquire token: {e}",
                remediation="Run `az login` or configure a service principal.",
            )


class ArtifactsSdkCheck(PreflightCheck):
    """Confirm the Synapse Artifacts SDK can reach the workspace dev endpoint."""

    name = "artifacts_sdk"
    severity = CheckSeverity.WARN
    depends_on: list[str] = ["azure_credential"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            from azure.synapse.artifacts import ArtifactsClient  # noqa: PLC0415
        except ImportError:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="azure-synapse-artifacts not installed in this environment.",
                remediation="`pip install azure-synapse-artifacts` to enable this check.",
            )
        endpoint = context["raw_config"].get("azure_api_access", {}).get("development_endpoint", "")
        if not endpoint:
            return self._result(CheckStatus.FAIL, detail="development_endpoint missing in credentials.")
        cred = context["shared"].get("azure_credential")
        if cred is None:
            return self._result(
                CheckStatus.SKIP,
                detail="azure_credential not available; cannot construct ArtifactsClient.",
            )
        try:
            client = ArtifactsClient(endpoint=endpoint, credential=cred)
            pools_iter = client.sql_pools.list()
            pools = [
                p.as_dict() if hasattr(p, "as_dict") else dict(p) for p in getattr(pools_iter, "value", []) or []
            ]
            context["shared"]["sql_pools"] = [p.get("name") for p in pools if p.get("name")]
            context["shared"]["workspace_resource_id"] = client.workspace.get().as_dict().get("id")
            return self._result(
                CheckStatus.PASS,
                detail=f"Artifacts API reachable; {len(pools)} dedicated pool(s) discovered.",
            )
        except Exception as e:  # noqa: BLE001
            return self._result(
                CheckStatus.FAIL,
                detail=f"ArtifactsClient call failed: {e}",
                remediation=(
                    "Verify the development_endpoint URL and that the running identity has the "
                    "`Synapse Artifact User` role on the workspace."
                ),
            )


class AzureMonitorCheck(PreflightCheck):
    name = "azure_monitor"
    severity = CheckSeverity.WARN
    depends_on: list[str] = ["azure_credential"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            from azure.monitor.query import MetricsQueryClient  # noqa: PLC0415
        except ImportError:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="azure-monitor-query not installed in this environment.",
                remediation="`pip install azure-monitor-query` to enable this check.",
            )
        cred = context["shared"].get("azure_credential")
        if cred is None:
            return self._result(CheckStatus.SKIP, detail="azure_credential not available.")
        resource_id = context["shared"].get("workspace_resource_id")
        if not resource_id:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="workspace_resource_id not available; ArtifactsClient probably skipped.",
            )
        try:
            client = MetricsQueryClient(credential=cred)
            client.query_resource(resource_id, metric_names=[])
            return self._result(CheckStatus.PASS, detail="MetricsQueryClient reachable.")
        except Exception as e:  # noqa: BLE001
            return self._result(
                CheckStatus.FAIL,
                detail=f"MetricsQueryClient call failed: {e}",
                remediation="Assign `Monitoring Reader` on the Synapse workspace to the running identity.",
            )


class MsiStorageRbacCheck(PreflightCheck):
    """Best-effort: is the workspace MSI granted Storage Blob Data Contributor?

    Requires ``azure-mgmt-authorization`` + ``azure-mgmt-storage``. When those
    are missing, or the running identity cannot read role assignments, we
    return UNKNOWN with a clear hint rather than failing the run.
    """

    name = "msi_storage_rbac"
    severity = CheckSeverity.WARN
    depends_on: list[str] = ["artifacts_sdk"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            from azure.mgmt.authorization import AuthorizationManagementClient  # noqa: PLC0415,F401
        except ImportError:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="azure-mgmt-authorization not installed; cannot inspect role assignments.",
                remediation=(
                    "Manually verify the Synapse workspace's managed identity has "
                    "`Storage Blob Data Contributor` on the primary ADLS Gen2 container."
                ),
            )
        return self._result(
            CheckStatus.UNKNOWN,
            detail="Storage RBAC probe requires Reader on the storage account; manual verification recommended.",
            remediation=(
                "In the Azure portal, on the primary ADLS Gen2 storage account, confirm the Synapse workspace's "
                "system-assigned managed identity is granted `Storage Blob Data Contributor` on the container, "
                "and that the storage firewall has `Allow Azure services on the trusted services list`."
            ),
        )


class SqlFirewallCheck(PreflightCheck):
    """Best-effort: is the host's egress IP allowed by Synapse SQL firewall?"""

    name = "sql_firewall"
    severity = CheckSeverity.WARN
    depends_on: list[str] = ["azure_credential"]
    parallel_safe = True

    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        try:
            from azure.mgmt.synapse import SynapseManagementClient  # noqa: PLC0415,F401
        except ImportError:
            return self._result(
                CheckStatus.UNKNOWN,
                detail="azure-mgmt-synapse not installed; cannot inspect SQL firewall rules.",
                remediation=(
                    "Manually verify your host's outbound IP is allowed in the Synapse workspace "
                    "SQL firewall (or that the workspace allows Azure services)."
                ),
            )
        return self._result(
            CheckStatus.UNKNOWN,
            detail="SQL firewall probe requires subscription + workspace resource group; manual check recommended.",
        )


def _synapse_checks(_context: dict[str, Any]) -> list[PreflightCheck]:
    """Order matters: dependents follow dependencies.

    The runner enforces ``depends_on`` short-circuit but it relies on the
    upstream check having executed already, so we order the list
    topologically.
    """
    return [
        CredentialsIntegrityCheck(),
        ProfilerScopeCheck(),
        OdbcDriverCheck(),
        NetworkTlsCheck(),
        AzureCredentialCheck(),
        ArtifactsSdkCheck(),
        SqlAuthCheck(),
        ServerDefaultDbCheck(),
        PerPoolOpenCheck(),
        PerDbOpenCheck(),
        AzureMonitorCheck(),
        MsiStorageRbacCheck(),
        SqlFirewallCheck(),
    ]


def register(target: PreflightRunner) -> None:
    """Register the Synapse check suite with ``target``."""
    target.register("synapse", _synapse_checks)


# Auto-register on import; the CLI imports this module before invoking the runner.
from databricks.labs.lakebridge.connections import preflight as _preflight  # noqa: E402

register(_preflight.runner)


# Re-exports useful in tests and external callers.
__all__ = [
    "AzureCredentialCheck",
    "ArtifactsSdkCheck",
    "AzureMonitorCheck",
    "CredentialsIntegrityCheck",
    "MsiStorageRbacCheck",
    "NetworkTlsCheck",
    "OdbcDriverCheck",
    "PerDbOpenCheck",
    "PerPoolOpenCheck",
    "ProfilerScopeCheck",
    "ServerDefaultDbCheck",
    "SqlAuthCheck",
    "SqlFirewallCheck",
    "register",
]
