import logging
import warnings

# google.api_core emits a FutureWarning about Python <3.11 EOL on import; not actionable here.
# Scope the suppression to the import so we don't mask the same warning elsewhere.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="You are using a Python version", category=FutureWarning)
    from google.api_core.exceptions import GoogleAPIError
    from google.cloud import bigquery

logger = logging.getLogger(__name__)


def validate_bigquery_pairs(raw_config: dict) -> None:
    """Validate connectivity to each configured BigQuery (project, region) pair.

    Each pair is probed independently (a trivial ``SELECT 1`` against that project/region)
    and all failures are aggregated into a single ``ConnectionError``.
    """
    pairs = raw_config.get("pairs", [])
    if not pairs:
        raise ValueError("No BigQuery project/region pairs configured")

    failures: dict[str, str] = {}
    for pair in pairs:
        label = f"{pair['project']}.{pair['region']}"
        logger.info(f"Testing connection to BigQuery {label}...")
        try:
            with bigquery.Client(project=pair["project"], location=pair["region"]) as client:
                client.query("SELECT 1").result()
            logger.info(f"BigQuery {label} connection successful")
        except GoogleAPIError as e:
            logger.error(f"Failed to connect to BigQuery {label}: {e}")
            failures[label] = str(e)

    if failures:
        details = "; ".join(f"{label}: {msg}" for label, msg in failures.items())
        raise ConnectionError(f"Connection failed for BigQuery pairs - {details}")
