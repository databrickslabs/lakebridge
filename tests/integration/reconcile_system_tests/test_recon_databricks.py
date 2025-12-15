from datetime import datetime, timezone, timedelta

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import TerminationTypeType

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    DatabaseConfig,
    ReconcileMetadataConfig,
    LakebridgeConfiguration,
    DeployReconcileConfig,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.blueprint.wheels import ProductInfo

from tests.integration.debug_envgetter import TestEnvGetter

TEST_JOBS_PURGE_TIMEOUT = timedelta(hours=1, minutes=15)


def get_test_purge_time() -> str:
    return (datetime.now(timezone.utc) + TEST_JOBS_PURGE_TIMEOUT).strftime("%Y%m%d%H")


TABLE_RECON_JSON = """
{
  "source_schema": "test_source",
  "target_catalog": "sandbox",
  "target_schema": "test_target",
  "tables": [
    {
      "source_name": "diamonds",
      "target_name": "diamonds",
      "join_columns": ["color", "clarity"]
    }
  ]
}
"""

test_env = TestEnvGetter(True)
cluster = test_env.get("TEST_DEFAULT_CLUSTER_ID")
date_to_remove = get_test_purge_time()
tags = {"RemoveAfter": date_to_remove}
deployment_overrides = DeployReconcileConfig(existing_cluster_id=cluster, tags=tags)

recon_config = ReconcileConfig(
    data_source="databricks",
    report_type="all",
    secret_scope="NOT_NEEDED",
    database_config=DatabaseConfig(
        source_catalog="sandbox", source_schema="test_source", target_catalog="sandbox", target_schema="test_target"
    ),
    metadata_config=ReconcileMetadataConfig(catalog="sandbox", schema="reconcile"),
    deployment_overrides=deployment_overrides,
)
config = LakebridgeConfiguration(None, recon_config)
source_catalog_or_schema = (
    recon_config.database_config.source_catalog
    if recon_config.database_config.source_catalog
    else recon_config.database_config.source_schema
)
filename = f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"


def test_recon_databricks_job_succeeds(ws: WorkspaceClient) -> None:
    ctx = ApplicationContext(ws)
    ctx.replace(product_info=ProductInfo.for_testing(LakebridgeConfiguration))
    ctx.installation.save(recon_config)
    ctx.installation.upload(filename, TABLE_RECON_JSON.encode())
    ctx.workspace_installation.install(config)

    recon_runner = ReconcileRunner(
        ctx.workspace_client,
        ctx.install_state,
    )
    run, _ = recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
    result = run.result()

    assert result.status
    assert result.status.termination_details
    assert result.status.termination_details.type
    assert result.status.termination_details.type.value == TerminationTypeType.SUCCESS.value
