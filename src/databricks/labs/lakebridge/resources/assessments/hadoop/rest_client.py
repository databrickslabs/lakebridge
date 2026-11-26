"""REST client shared by the Hadoop profiler steps.

- one ``requests`` session per base URL, with optional basic auth and TLS verification on, off or against a CA bundle;
- connect and read timeouts, and a short retry on connection errors only;
- ``HadoopApiError`` for unreachable services, HTTP errors, timeouts and non-JSON bodies (such as a login page);
- ResourceManager HA: the client for the RM whose ``haState`` is ``ACTIVE``.

A base URL may carry a path, as an HDInsight gateway does (``https://<cluster>.azurehdinsight.net/yarnui``). Kerberos
(SPNEGO) is in ``kerberos.py``, a ``RestClient`` subclass.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10.0
# Generous: application dumps on busy clusters are large and slow.
READ_TIMEOUT_SECONDS = 300.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0

# The characters a JSON text can start with.
JSON_FIRST_CHARACTERS = frozenset('{["-0123456789tfn')

CLUSTER_INFO_PATH = "/ws/v1/cluster/info"
ACTIVE_HA_STATE = "ACTIVE"

_HTTP_STATUS_HINTS = {
    401: " (check the username and password)",
    403: " (check the username and password, and the account's permissions)",
    404: " (check the URL path; a gateway may need its own path prefix)",
}


class HttpResponse(Protocol):
    """The part of a response the client reads (``requests.Response`` or the Kerberos transport's)."""

    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


class HadoopApiError(RuntimeError):
    """A Hadoop service is unreachable, returned an error status, or did not return JSON."""


def not_json_error(path: str, base_url: str) -> HadoopApiError:
    return HadoopApiError(
        f"GET {path} from {base_url} did not return JSON; check the URL (a gateway may need its path) and the credentials"
    )


def parse_json(text: str, path: str, base_url: str) -> Any:
    """Parse a ``get_text`` body, reporting malformed JSON as ``get_json`` does."""
    try:
        return json.loads(text)
    except ValueError as e:
        raise not_json_error(path, base_url) from e


class HadoopHttpError(HadoopApiError):
    """A Hadoop service returned an HTTP error status, kept with the body so callers can handle known errors."""

    def __init__(self, message: str, *, status_code: int, body: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def normalize_base_url(url: str) -> str:
    """Validate a ``http(s)://host[:port][/path]`` base URL and return it without a trailing slash.

    Credentials in the URL are rejected so they cannot leak into error messages.
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
        """GET ``path`` and return the body, which must look like JSON.

        Only the first character is checked, which catches login pages and plain text; large bodies are parsed once, by
        DuckDB, which rejects malformed JSON when storing them.
        """
        text = self.get_plain_text(path, params)
        if text.lstrip()[:1] not in JSON_FIRST_CHARACTERS:
            raise self._not_json(path)
        return text

    def _not_json(self, path: str) -> HadoopApiError:
        return not_json_error(path, self._base_url)

    def get_plain_text(self, path: str, params: Mapping[str, str | int] | None = None) -> str:
        """GET ``path`` and return the body as is, for plain-text endpoints (Cloudera Manager's /api/version)."""
        response = self._get(path, params)
        if response.status_code >= 400:
            raise HadoopHttpError(
                self._http_error_message(path, response.status_code),
                status_code=response.status_code,
                body=response.text,
            )
        return response.text

    def _http_error_message(self, path: str, status_code: int) -> str:
        hint = _HTTP_STATUS_HINTS.get(status_code, "")
        return f"GET {path} from {self._base_url} returned HTTP {status_code}{hint}"

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return parse_json(self.get_text(path, params), path, self._base_url)

    def _get(self, path: str, params: Mapping[str, str | int] | None) -> HttpResponse:
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
    make_client: Callable[[str], RestClient] | None = None,
) -> RestClient:
    """Return the client for the ResourceManager in HA state ACTIVE (a non-HA RM reports ACTIVE too).

    URLs are tried in order; unreachable or standby RMs are skipped, and the error says why each was rejected.
    ``make_client`` builds each client (for example a Kerberos one); the default is a plain ``RestClient``.
    """
    build = make_client or (lambda url: RestClient(url, username=username, password=password, verify=verify))
    failures: list[str] = []
    for url in urls:
        client = build(url)
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
