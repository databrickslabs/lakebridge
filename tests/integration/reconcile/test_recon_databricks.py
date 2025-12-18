import pytest

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


@pytest.fixture
def recon_config(watchdog_remove_after: str) -> ReconcileConfig:
    test_env = TestEnvGetter(True)
    cluster = test_env.get("TEST_DEFAULT_CLUSTER_ID")
    tags = {"RemoveAfter": watchdog_remove_after}
    deployment_overrides = DeployReconcileConfig(existing_cluster_id=cluster, tags=tags)

    conf = ReconcileConfig(
        data_source="databricks",
        report_type="all",
        secret_scope="NOT_NEEDED",
        database_config=DatabaseConfig(
            source_catalog="sandbox", source_schema="test_source", target_catalog="sandbox", target_schema="test_target"
        ),
        metadata_config=ReconcileMetadataConfig(catalog="sandbox", schema="reconcile"),
        deployment_overrides=deployment_overrides,
    )
    return conf


@pytest.fixture
def recon_config_filename(recon_config: ReconcileConfig) -> str:
    source_catalog_or_schema = (
        recon_config.database_config.source_catalog
        if recon_config.database_config.source_catalog
        else recon_config.database_config.source_schema
    )
    filename = f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"
    return filename


def test_recon_databricks_job_succeeds(
    ws: WorkspaceClient, recon_config: ReconcileConfig, recon_config_filename: str
) -> None:
    ctx = ApplicationContext(ws).replace(product_info=ProductInfo.for_testing(LakebridgeConfiguration))
    ctx.installation.save(recon_config)
    ctx.installation.upload(recon_config_filename, TABLE_RECON_JSON.encode())
    ctx.workspace_installation.install(LakebridgeConfiguration(None, recon_config))

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
