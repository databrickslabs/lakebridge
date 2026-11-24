"""REST client shared by the Hadoop profiler steps.

Every step reads from a YARN ResourceManager (``/ws/v1/cluster/...``) or a Spark History Server (``/api/v1/...``)
through this client. It provides:

- one ``requests`` session per base URL, with optional HTTP basic auth and TLS verification on, off, or against a
  CA bundle;
- explicit connect and read timeouts, and a short retry on connection errors only;
- ``HadoopApiError`` for unreachable endpoints, HTTP errors, timeouts and non-JSON bodies (for example a gateway's
  HTML login page);
- ResourceManager HA: the client for the RM whose ``/ws/v1/cluster/info`` reports ``haState == "ACTIVE"``.

A base URL may carry a path and no port. An HDInsight gateway, for example, is
``https://<cluster>.azurehdinsight.net/yarnui``. Kerberos is not implemented in this phase.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10.0
# Application dumps on busy clusters are large and slow to render, so the read timeout is generous.
READ_TIMEOUT_SECONDS = 300.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0

CLUSTER_INFO_PATH = "/ws/v1/cluster/info"
ACTIVE_HA_STATE = "ACTIVE"

_HTTP_STATUS_HINTS = {
    401: " (check the username and password)",
    403: " (check the username and password, and the account's permissions)",
    404: " (check the URL path; a gateway may need its own path prefix)",
}


class HadoopApiError(RuntimeError):
    """A Hadoop service is unreachable, returned an error status, or did not return JSON."""


class HadoopHttpError(HadoopApiError):
    """A Hadoop service returned an HTTP error status. The status and body let a caller handle a known error."""

    def __init__(self, message: str, *, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def normalize_base_url(url: str) -> str:
    """Validate a service base URL and return it without a trailing slash.

    The URL must be ``http(s)://host[:port][/path]``. Credentials are rejected here so they cannot leak into error
    messages; they belong in the ``username`` and ``password`` settings.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Invalid Hadoop service URL {url!r}: expected http(s)://host[:port][/path]")
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            f"Invalid Hadoop service URL {url!r}: put credentials in username and password, not in the URL"
        )
    if parts.query or parts.fragment:
        raise ValueError(f"Invalid Hadoop service URL {url!r}: remove the query string and fragment")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class RestClient:
    """Read-only GET client for one Hadoop service base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        verify: bool | str = True,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        self._session = session if session is not None else requests.Session()
        self._session.verify = verify
        if username is not None:
            self._session.auth = (username, password or "")
            if self._base_url.startswith("http://"):
                logger.warning(f"Sending credentials to {self._base_url} over plain HTTP")
        if verify is False:
            logger.warning(f"TLS certificate verification is disabled for {self._base_url}")
        self._sleep = sleep if sleep is not None else time.sleep

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_text(self, path: str, params: Mapping[str, str | int] | None = None) -> str:
        """GET ``path`` and return the response body, which is checked to be JSON."""
        text = self.get_plain_text(path, params)
        try:
            json.loads(text)
        except ValueError as e:
            raise HadoopApiError(
                f"GET {path} from {self._base_url} did not return JSON; check the URL "
                "(a gateway may need its path) and the credentials"
            ) from e
        return text

    def get_plain_text(self, path: str, params: Mapping[str, str | int] | None = None) -> str:
        """GET ``path`` and return the body as is, for endpoints that answer in plain text (Cloudera's /api/version)."""
        response = self._get(path, params)
        if response.status_code >= 400:
            hint = _HTTP_STATUS_HINTS.get(response.status_code, "")
            raise HadoopHttpError(
                f"GET {path} from {self._base_url} returned HTTP {response.status_code}{hint}",
                status_code=response.status_code,
                body=response.text,
            )
        return response.text

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return json.loads(self.get_text(path, params))

    def _get(self, path: str, params: Mapping[str, str | int] | None) -> requests.Response:
        url = f"{self._base_url}{path}"
        attempt = 1
        while True:
            try:
                return self._session.get(url, params=params, timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS))
            except requests.ConnectionError as e:
                if attempt >= MAX_ATTEMPTS:
                    raise HadoopApiError(
                        f"GET {path} from {self._base_url} failed after {MAX_ATTEMPTS} attempts: " f"{type(e).__name__}"
                    ) from e
                logger.warning(f"GET {path} from {self._base_url} failed ({type(e).__name__}); retrying")
                self._sleep(RETRY_BACKOFF_SECONDS * attempt)
                attempt += 1
            except requests.Timeout as e:
                raise HadoopApiError(f"GET {path} from {self._base_url} timed out") from e
            except requests.RequestException as e:
                raise HadoopApiError(f"GET {path} from {self._base_url} failed: {type(e).__name__}") from e


def find_active_resource_manager(
    urls: Sequence[str],
    *,
    username: str | None = None,
    password: str | None = None,
    verify: bool | str = True,
) -> RestClient:
    """Return the client for the ResourceManager in HA state ACTIVE.

    The URLs are tried in order. A non-HA cluster also reports ``haState`` ACTIVE, so it needs no special case.
    Unreachable or standby ResourceManagers are skipped, and the error lists why each one was rejected.
    """
    failures: list[str] = []
    for url in urls:
        client = RestClient(url, username=username, password=password, verify=verify)
        try:
            info = client.get_json(CLUSTER_INFO_PATH)
        except HadoopApiError as e:
            failures.append(str(e))
            continue
        cluster_info = info.get("clusterInfo") if isinstance(info, dict) else None
        ha_state = cluster_info.get("haState") if isinstance(cluster_info, dict) else None
        if ha_state == ACTIVE_HA_STATE:
            logger.info(f"Using the active ResourceManager at {client.base_url}")
            return client
        failures.append(f"{client.base_url} reports haState {ha_state!r}")
    raise HadoopApiError("No ACTIVE ResourceManager found: " + "; ".join(failures))
