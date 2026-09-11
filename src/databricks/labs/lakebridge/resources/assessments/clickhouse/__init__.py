# Managed ClickHouse Cloud service hostnames always end in this suffix; a definitive Cloud signal.
# Single source of truth shared by the variant resolver, the connector, and the cost-enrichment step.
CLICKHOUSE_CLOUD_HOST_SUFFIX = ".clickhouse.cloud"

# Default HTTP ports: TLS on 8443 (Cloud), plaintext on 8123 (self-managed / OSS).
CLICKHOUSE_SECURE_PORT = 8443
CLICKHOUSE_PLAINTEXT_PORT = 8123


def is_cloud_host(host: str | None) -> bool:
    """Return True when ``host`` is a managed ClickHouse Cloud hostname (``*.clickhouse.cloud``)."""
    return str(host or "").strip().lower().endswith(CLICKHOUSE_CLOUD_HOST_SUFFIX)
