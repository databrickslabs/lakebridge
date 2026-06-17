import logging

from google.cloud import bigquery

logger = logging.getLogger(__name__)


def validate_bigquery_pairs(raw_config: dict) -> None:
    """Validate connectivity to each configured BigQuery (project, region) pair.

    Mirrors ``validate_synapse_pools``: each pair is probed independently (a trivial
    ``SELECT 1`` against that project/region) and all failures are aggregated into a
    single ``ConnectionError``. Authentication uses the standard ADC chain.
    """
    pairs = raw_config.get("pairs", [])
    if not pairs:
        raise ValueError("No BigQuery project/region pairs configured")

    failures: dict[str, str] = {}
    for pair in pairs:
        label = f"{pair['project']}.{pair['region']}"
        logger.info(f"Testing connection to BigQuery {label}...")
        try:
            client = bigquery.Client(project=pair["project"], location=pair["region"])
            client.query("SELECT 1").result()
            logger.info(f"BigQuery {label} connection successful")
        except Exception as e:
            logger.error(f"Failed to connect to BigQuery {label}: {e}")
            failures[label] = str(e)

    if failures:
        details = "; ".join(f"{label}: {msg}" for label, msg in failures.items())
        raise ConnectionError(f"Connection failed for BigQuery pairs - {details}")
