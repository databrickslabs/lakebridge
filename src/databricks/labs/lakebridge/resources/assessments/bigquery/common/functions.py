import argparse
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.cloud import bigquery

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
        logger.error(msg)
        raise ValueError(msg)

    return args.db_path, credential_file


def create_bigquery_client(project_id: str, region: str) -> "bigquery.Client":
    """Create a google-cloud-bigquery Client routed at the given project + region.

    Authentication uses the standard ADC chain (GOOGLE_APPLICATION_CREDENTIALS, gcloud
    application-default login, or the metadata server). Service-account impersonation is
    supported via ADC; see
    https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment#sa-impersonation.
    """
    # Import here to keep this module importable in environments where the BQ client isn't
    # installed (e.g. unit tests that mock the client).
    from google.cloud import bigquery  # pylint: disable=import-outside-toplevel

    return bigquery.Client(project=project_id, location=region)
