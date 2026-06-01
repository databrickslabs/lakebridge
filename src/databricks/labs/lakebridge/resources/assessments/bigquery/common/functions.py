import logging

from google.cloud import bigquery

logger = logging.getLogger(__name__)


def create_bigquery_client(project_id: str, region: str) -> bigquery.Client:
    """Create a google-cloud-bigquery Client routed at the given project + region.

    Authentication uses the standard ADC chain (GOOGLE_APPLICATION_CREDENTIALS, gcloud
    application-default login, or the metadata server). Service-account impersonation is
    supported via ADC; see
    https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment#sa-impersonation.
    """
    return bigquery.Client(project=project_id, location=region)
