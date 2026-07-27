import json
import sys
from typing import Callable

import urllib3
from azure.monitor.query import MetricsQueryClient
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import CredentialManager, create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    create_azure_metrics_query_client,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseMetrics

logger = get_logger(__file__)


def build_resource_id(subscription_id: str, resource_group: str, server_fqdn: str, database: str) -> str:
    """Build the ARM resource id for a standalone dedicated SQL pool.

    Unlike a Synapse workspace pool (whose resource id is returned by the control-plane
    API), a standalone pool is a ``Microsoft.Sql/servers/databases`` resource we must
    address ourselves. The server's short name is the first label of its FQDN
    (e.g. ``my-dw-server`` from ``my-dw-server.database.windows.net``).
    """
    server_name = server_fqdn.split(".")[0]
    return (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Sql/servers/{server_name}"
        f"/databases/{database}"
    )


def execute(
    credential_manager: CredentialManager,
    metrics_client_factory: Callable[[], MetricsQueryClient],
    db_path: str,
) -> None:
    settings = credential_manager.get_credentials("legacy_synapse")

    try:
        azure = settings.get("azure")
        if not azure or not azure.get("subscription_id") or not azure.get("resource_group"):
            raise ValueError(
                "Missing Azure settings for monitoring metrics. Re-run "
                "`configure-database-profiler` for legacy_synapse and provide the "
                "subscription ID and resource group."
            )

        resource_id = build_resource_id(
            azure["subscription_id"], azure["resource_group"], settings["server"], settings["database"]
        )
        logger.info(f"dedicated pool resource_id: {resource_id}")

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        synapse_metrics = SynapseMetrics(metrics_client_factory())

        metrics_df = synapse_metrics.get_sql_dw_metrics(resource_id)
        if not metrics_df.empty:
            metrics_df.insert(loc=0, column="pool_name", value=settings["database"])
        save_to_duckdb(metrics_df, "metrics_dedicated_pool_metrics", db_path)

        # pipeline._run_python_script parses the LAST stdout line as the step's JSON result,
        # so the success payload must be printed to stdout (not logged) to be seen as success.
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        # No stdout payload on failure: the non-zero exit code fails the step, and the pipeline
        # reports the error from the logs.
        logger.error(f"Failed to extract Legacy Synapse Monitoring Metrics: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    initialize_logging()
    _db_path, _creds_file = arguments_loader(desc="Legacy Synapse Monitoring Metrics Extract Script")
    execute(
        credential_manager=create_credential_manager(PRODUCT_NAME, EnvGetter(), _creds_file),
        metrics_client_factory=create_azure_metrics_query_client,
        db_path=_db_path,
    )
