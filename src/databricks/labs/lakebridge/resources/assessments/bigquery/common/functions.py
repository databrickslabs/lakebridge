import argparse
import json
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def arguments_loader(desc: str) -> tuple[str, str]:
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--db-path', type=str, required=True, help='Path to DuckDB database file')
    parser.add_argument(
        '--credential-config-path', type=str, required=True, help='Path string containing credential configuration'
    )
    args = parser.parse_args()
    credential_file = args.credential_config_path

    if not credential_file.endswith('credentials.yml'):
        msg = "Credential config file must have 'credentials.yml' extension"
        print(json.dumps({"status": "error", "message": msg}), file=sys.stderr)
        raise ValueError(msg)

    return args.db_path, credential_file


def create_bigquery_client(project_id: str, region: str, sa_key_path: Optional[str] = None):
    """
    Create a google-cloud-bigquery Client for the given project + region.

    Auth precedence:
      1. If `sa_key_path` is provided, load credentials from that JSON file.
         A missing/unreadable file raises FileNotFoundError — we never silently
         fall back to ADC because that would hide misconfiguration.
      2. Otherwise, defer to Google's default credential chain
         (GOOGLE_APPLICATION_CREDENTIALS env var, then ADC, then metadata server).
    """
    # Validate SA key path before importing the BQ client. This lets the misconfiguration
    # error surface even in environments where google-cloud-bigquery is not yet installed.
    if sa_key_path and not os.path.isfile(sa_key_path):
        raise FileNotFoundError(
            f"Service account key file not found: {sa_key_path}. "
            "Provide a valid path or leave the field blank to use Application Default Credentials."
        )

    # Import here to keep this module importable in environments where the BQ
    # client isn't installed (e.g. unit tests that mock the client).
    from google.cloud import bigquery  # type: ignore[import-untyped]
    from google.oauth2 import service_account  # type: ignore[import-untyped]

    if sa_key_path:
        credentials = service_account.Credentials.from_service_account_file(sa_key_path)
        return bigquery.Client(project=project_id, location=region, credentials=credentials)

    return bigquery.Client(project=project_id, location=region)
