from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    DatabaseConfig,
    ReconcileMetadataConfig,
    LakebridgeConfiguration,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner

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

recon_config = ReconcileConfig(
    data_source="databricks",
    report_type="all",
    secret_scope="NOT_NEEDED",
    database_config=DatabaseConfig(
        source_catalog="sandbox", source_schema="test_source", target_catalog="sandbox", target_schema="test_target"
    ),
    metadata_config=ReconcileMetadataConfig(catalog="sandbox", schema="reconcile"),
    job_id="e2e_test_recon_job",
)
config = LakebridgeConfiguration(None, recon_config)
source_catalog_or_schema = (
    recon_config.database_config.source_catalog
    if recon_config.database_config.source_catalog
    else recon_config.database_config.source_schema
)
filename = f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"


def test_recon():
    ws = WorkspaceClient(product="lakebridge acceptance")
    ctx = ApplicationContext(ws)
    recon_runner = ReconcileRunner(
        ctx.workspace_client,
        ctx.installation,
        ctx.install_state,
        ctx.prompts,
    )

    ctx.installation.save(recon_config)
    ctx.installation.save(recon_config, filename=filename)
    ctx.workspace_installation.install(config)
    recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
    assert True
