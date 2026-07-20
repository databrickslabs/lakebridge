# Managed ClickHouse Cloud service hostnames always end in this suffix; a definitive Cloud signal.
# Single source of truth shared by the variant resolver and the costs collector's Cloud detection.
CLICKHOUSE_CLOUD_HOST_SUFFIX = ".clickhouse.cloud"

# Default HTTP ports: TLS on 8443 (Cloud), plaintext on 8123 (self-managed / OSS).
CLICKHOUSE_SECURE_PORT = 8443
CLICKHOUSE_PLAINTEXT_PORT = 8123

# Truthy string tokens accepted for a boolean credential value (e.g. `secure`) written by hand into a
# credentials file. Anything else (including the string "false") is falsey — note that bool("false")
# is True in Python, so this must never be replaced with a bare bool() coercion.
_TRUTHY = {"true", "yes", "1", "on"}


def is_cloud_host(host: str | None) -> bool:
    """Return True when ``host`` is a managed ClickHouse Cloud hostname (``*.clickhouse.cloud``)."""
    return str(host or "").strip().lower().endswith(CLICKHOUSE_CLOUD_HOST_SUFFIX)


def parse_bool(value: object, default: bool = False) -> bool:
    """Parse a credential value that may be a real bool or a string like ``"false"``.

    ``bool("false")`` is ``True`` in Python, so a hand-written credentials file that stores
    ``secure: "false"`` must be parsed on the string content, not coerced with ``bool()``. A real
    bool passes through; a string is matched case-insensitively against the truthy token set;
    anything else falls back to ``default``.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def normalize_secure_and_port(config: dict) -> tuple[bool, int]:
    """Resolve the ``(secure, port)`` an extraction/probe connection should use, host-derived.

    Both the collector-facing ``ClickHouseConnection`` and the probe ``ClickHouseConnector`` call this
    so a hand-written credentials file behaves identically on either path:

    - A ``*.clickhouse.cloud`` host is **forced** to ``secure=True`` — managed Cloud only accepts TLS,
      and this connection carries the password, so it must never go plaintext-by-default (or be
      downgraded by a stray ``secure: false``) against a Cloud host.
    - Otherwise ``secure`` follows the configured value (parsed safely, see ``parse_bool``), defaulting
      to ``False`` for self-managed / OSS.
    - ``port`` follows the configured value when present, else the TLS/plaintext default for the
      resolved ``secure``.
    """
    host = config.get("host")
    cloud = is_cloud_host(host)
    secure = True if cloud else parse_bool(config.get("secure"), default=False)
    default_port = CLICKHOUSE_SECURE_PORT if secure else CLICKHOUSE_PLAINTEXT_PORT
    port_value = config.get("port")
    port = default_port if port_value in (None, "") else int(str(port_value))
    return secure, port
