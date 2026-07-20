import json
import sys

import urllib3
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    create_azure_metrics_query_client,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseMetrics

logger = get_logger(__file__)


def build_resource_id(azure: dict, server_fqdn: str, database: str) -> str:
    """Build the ARM resource id for a standalone dedicated SQL pool.

    Unlike a Synapse workspace pool (whose resource id is returned by the control-plane
    API), a standalone pool is a ``Microsoft.Sql/servers/databases`` resource we must
    address ourselves. The server's short name is the first label of its FQDN
    (e.g. ``my-dw-server`` from ``my-dw-server.database.windows.net``).
    """
    server_name = server_fqdn.split(".")[0]
    return (
        f"/subscriptions/{azure['subscription_id']}"
        f"/resourceGroups/{azure['resource_group']}"
        f"/providers/Microsoft.Sql/servers/{server_name}"
        f"/databases/{database}"
    )


def execute():
    db_path, creds_file = arguments_loader(desc="Legacy Synapse Monitoring Metrics Extract Script")
    cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_file)
    settings = cred_manager.get_credentials("legacy_synapse")

    try:
        azure = settings.get("azure")
        if not azure or not azure.get("subscription_id") or not azure.get("resource_group"):
            raise ValueError(
                "Missing Azure settings for monitoring metrics. Re-run "
                "`configure-database-profiler` for legacy_synapse and provide the "
                "subscription ID and resource group."
            )

        resource_id = build_resource_id(azure, settings["server"], settings["database"])
        logger.info(f"dedicated pool resource_id: {resource_id}")

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        metrics_client = create_azure_metrics_query_client()
        synapse_metrics = SynapseMetrics(metrics_client)

        metrics_df = synapse_metrics.get_sql_dw_metrics(resource_id)
        if not metrics_df.empty:
            metrics_df.insert(loc=0, column="pool_name", value=settings["database"])
        save_to_duckdb(metrics_df, "metrics_dedicated_pool_metrics", db_path)

        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        logger.error(f"Failed to extract Legacy Synapse Monitoring Metrics: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    initialize_logging()
    execute()
