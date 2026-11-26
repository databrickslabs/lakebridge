"""Kerberos (SPNEGO) transport for the ResourceManager and the Spark History Server.

As the original profiler, requests run through ``curl --negotiate -u :`` with the system Kerberos tools:

- with a ``keytab``, ``kinit -kt`` runs unless the cache already holds a valid ticket for the principal, so a run's steps
  (one process each) share one ticket. Without ``ccache`` the profiler uses its own cache (``profiler_ccache``), leaving
  the account's default cache alone;
- without one, ``klist -s`` must find a valid ticket.

Subprocesses get argument lists, never shell strings. curl exits 0 on HTTP 401, so the status comes from ``-w`` and the
body from an ``-o`` file.
"""

import functools
import getpass
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    READ_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    HadoopApiError,
    HttpResponse,
    RestClient,
)

logger = logging.getLogger(__name__)

# curl's "-V" feature names that mean SPNEGO is available.
SPNEGO_FEATURES = ("SPNEGO", "GSS-API", "Kerberos")
# curl exit codes: 6 unresolved host and 7 no connection (both retried), 28 timed out.
CURL_EXIT_UNREACHABLE = (6, 7)
CURL_EXIT_TIMEOUT = 28
KLIST_PRINCIPAL = re.compile(r"^\s*(?:Default principal|Principal):\s*(\S+)", re.MULTILINE)
NO_TICKET_MESSAGE = (
    "No valid Kerberos ticket{for_principal} (none in the ticket cache, or it expired). Run kinit for it, or set keytab "
    "and principal in the credentials file so the profiler runs kinit itself"
)


class KerberosError(HadoopApiError):
    """Kerberos cannot be used: a tool is missing, curl has no SPNEGO, or there is no valid ticket."""


def profiler_ccache(principal: str) -> str:
    """The profiler's own ticket cache, one per account and principal in the temporary directory."""
    try:
        account = getpass.getuser()
    except (KeyError, OSError):  # no user name available
        account = str(os.getuid()) if hasattr(os, "getuid") else "user"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", f"lakebridge-hadoop-krb5cc-{account}-{principal}")
    return f"FILE:{os.path.join(tempfile.gettempdir(), name)}"


@dataclass(frozen=True)
class KerberosSettings:
    """How the profiler obtains its ticket. ``keytab`` needs ``principal``; ``ccache`` and ``krb5_config`` are optional."""

    principal: str | None = None
    keytab: str | None = None
    ccache: str | None = None
    krb5_config: str | None = None

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.ccache:
            env["KRB5CCNAME"] = self.ccache
        elif self.keytab and self.principal:
            env["KRB5CCNAME"] = profiler_ccache(self.principal)
        if self.krb5_config:
            env["KRB5_CONFIG"] = self.krb5_config
        return env


@dataclass(frozen=True)
class CurlResponse:
    status_code: int
    text: str


class CurlFailure(Exception):
    """A curl request failed without an HTTP status; ``kind`` decides whether it is retried."""

    def __init__(self, kind: Literal["unreachable", "timeout", "failed"], detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise KerberosError(
            f"Kerberos authentication needs '{name}' on PATH; install the Kerberos client tools (MIT krb5 or Heimdal)"
        )
    return path


def _run(argv: list[str], env: Mapping[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, env=dict(env), capture_output=True, text=True, timeout=timeout, check=False)


def check_curl_has_spnego(env: Mapping[str, str]) -> None:
    curl = _require_tool("curl")
    result = _run([curl, "-V"], env, timeout=30)
    if not any(feature in result.stdout for feature in SPNEGO_FEATURES):
        raise KerberosError(
            "this curl has no Kerberos/SPNEGO support (curl -V lists no SPNEGO or GSS-API feature); install a curl "
            "built with GSS-API"
        )


def cached_principal(env: Mapping[str, str]) -> str | None:
    """The principal of the valid ticket in ``env``'s cache, or None. Reads MIT and Heimdal klist output."""
    klist = shutil.which("klist")
    if klist is None or _run([klist, "-s"], env, timeout=30).returncode != 0:
        return None
    match = KLIST_PRINCIPAL.search(_run([klist], env, timeout=30).stdout)
    return match.group(1) if match else None


def ensure_ticket(settings: KerberosSettings, env: Mapping[str, str]) -> None:
    """With a keytab, reuse a valid ticket for the principal or run ``kinit -kt``; without one, require a valid ticket."""
    if settings.keytab:
        if not settings.principal:
            raise KerberosError("hadoop: a keytab needs a principal")
        if cached_principal(env) == settings.principal:
            logger.debug(f"Reusing the valid Kerberos ticket for {settings.principal}")
            return
        kinit = _require_tool("kinit")
        result = _run([kinit, "-kt", settings.keytab, settings.principal], env, timeout=60)
        if result.returncode != 0:
            detail = result.stderr.strip() or "no message"
            raise KerberosError(
                f"kinit -kt {settings.keytab} {settings.principal} failed (exit {result.returncode}): {detail}. "
                "Check the keytab, the principal and the krb5_config (KDC address and realm)"
            )
        return
    klist = _require_tool("klist")
    if _run([klist, "-s"], env, timeout=30).returncode != 0:
        for_principal = f" for {settings.principal}" if settings.principal else ""
        raise KerberosError(NO_TICKET_MESSAGE.format(for_principal=for_principal))


@functools.lru_cache(maxsize=None)
def prepare(settings: KerberosSettings) -> None:
    """Check curl and the ticket once per process and settings (failures are not cached)."""
    env = settings.environment()
    check_curl_has_spnego(env)
    ensure_ticket(settings, env)
    logger.info("Kerberos ticket is ready" + (f" for {settings.principal}" if settings.principal else ""))


def curl_argv(curl: str, url: str, *, verify: bool | str, body_path: str) -> list[str]:
    """The curl command line. No ``-q``, so ``.curlrc`` still applies, as for the original."""
    argv = [
        curl,
        "-sS",
        "--negotiate",
        "-u",
        ":",
        "--connect-timeout",
        f"{CONNECT_TIMEOUT_SECONDS:g}",
        "--max-time",
        f"{READ_TIMEOUT_SECONDS:g}",
        "-H",
        "Accept: application/json",
        "-o",
        body_path,
        "-w",
        "%{http_code}",
    ]
    if verify is False:
        argv.append("-k")
    elif isinstance(verify, str):
        argv += ["--cacert", verify]
    argv.append(url)
    return argv


def curl_get(url: str, *, verify: bool | str, env: Mapping[str, str]) -> CurlResponse:
    """GET ``url`` with SPNEGO, returning the status and body even for error statuses."""
    curl = _require_tool("curl")
    descriptor, body_path = tempfile.mkstemp(prefix="lakebridge-hadoop-", suffix=".body")
    os.close(descriptor)
    try:
        argv = curl_argv(curl, url, verify=verify, body_path=body_path)
        try:
            result = _run(argv, env, timeout=CONNECT_TIMEOUT_SECONDS + READ_TIMEOUT_SECONDS + 30)
        except subprocess.TimeoutExpired as e:
            raise CurlFailure("timeout", "the request timed out") from e
        if result.returncode == CURL_EXIT_TIMEOUT:
            raise CurlFailure("timeout", "the request timed out")
        if result.returncode in CURL_EXIT_UNREACHABLE:
            raise CurlFailure("unreachable", f"curl exit {result.returncode}")
        if result.returncode != 0:
            first_line = (result.stderr.strip().splitlines() or ["no message"])[0]
            raise CurlFailure("failed", f"curl exit {result.returncode}: {first_line}")
        try:
            status_code = int(result.stdout.strip())
        except ValueError as e:
            raise CurlFailure("failed", "curl returned no HTTP status") from e
        with open(body_path, encoding="utf-8", errors="replace") as body_file:
            return CurlResponse(status_code=status_code, text=body_file.read())
    finally:
        os.unlink(body_path)


def _url_with_params(url: str, params: Mapping[str, str | int] | None) -> str:
    """Append URL-encoded parameters, keeping any query already there (such as ``?json``)."""
    if not params:
        return url
    parts = urlsplit(url)
    query = "&".join(part for part in (parts.query, urlencode(params)) if part)
    return urlunsplit(parts._replace(query=query))


class KerberosRestClient(RestClient):
    """``RestClient`` over curl with SPNEGO."""

    def __init__(
        self,
        base_url: str,
        *,
        kerberos: KerberosSettings,
        verify: bool | str = True,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(base_url, verify=verify, sleep=sleep)
        self._kerberos_env = kerberos.environment()
        self._verify = verify
        prepare(kerberos)

    def _http_error_message(self, path: str, status_code: int) -> str:
        if status_code != 401:
            return super()._http_error_message(path, status_code)
        host = urlsplit(self.base_url).hostname
        return (
            f"GET {path} from {self.base_url} failed: Kerberos authentication failed (HTTP 401): no valid ticket "
            "(run kinit or set keytab and principal), the ticket expired, or the URL host does not match the service "
            f"principal HTTP/{host}; use the host name the service is registered under, not an IP address or an alias"
        )

    def _get(self, path: str, params: Mapping[str, str | int] | None) -> HttpResponse:
        url = _url_with_params(f"{self.base_url}{path}", params)
        attempt = 1
        while True:
            try:
                return curl_get(url, verify=self._verify, env=self._kerberos_env)
            except CurlFailure as e:
                if e.kind == "timeout":
                    raise HadoopApiError(f"GET {path} from {self.base_url} timed out") from e
                if e.kind == "failed":
                    raise HadoopApiError(f"GET {path} from {self.base_url} failed: {e}") from e
                if attempt >= MAX_ATTEMPTS:
                    raise HadoopApiError(
                        f"GET {path} from {self.base_url} failed after {MAX_ATTEMPTS} attempts: {e}"
                    ) from e
                logger.warning(f"GET {path} from {self.base_url} failed ({e}); retrying")
                self._sleep(RETRY_BACKOFF_SECONDS * attempt)
                attempt += 1
