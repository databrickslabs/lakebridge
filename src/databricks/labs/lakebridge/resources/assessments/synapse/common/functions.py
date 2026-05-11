import logging

from azure.identity import DefaultAzureCredential
from azure.monitor.query import MetricsQueryClient
from azure.synapse.artifacts import ArtifactsClient

# Deprecated re-export: ``arguments_loader`` now lives in ``assessments.common.cli``.
# Kept here so unmigrated callers (Synapse and MSSQL extracts) keep working until
# they are migrated; the re-export will be removed once all imports are updated.
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader  # noqa: F401

logger = logging.getLogger(__name__)


def create_synapse_artifacts_client(config: dict) -> ArtifactsClient:
    """
    :return:  an Azure SDK client handle for Synapse Artifacts
    """
    return ArtifactsClient(
        endpoint=config["azure_api_access"]["development_endpoint"], credential=DefaultAzureCredential()
    )


def create_azure_metrics_query_client():
    """
    :return: an Azure SDK Monitoring Metrics Client handle
    """
    return MetricsQueryClient(credential=DefaultAzureCredential())
