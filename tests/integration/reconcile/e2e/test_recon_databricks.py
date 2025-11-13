from databricks.labs.lakebridge.config import ReconcileConfig, DatabaseConfig, ReconcileMetadataConfig, \
    LakebridgeConfiguration, ReconcileTablesConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import Table, RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
table_recon_json = """
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

table_recon_yaml = """
source_schema: test_source
target_catalog: sandbox
target_schema: test_target
tables:
  - source_name: diamonds
    target_name: diamonds
    join_columns:
      - color
      - clarity
"""

reconcile_config = ReconcileConfig(
    data_source = "databricks",
    report_type = "all",
    secret_scope = "NOT_NEEDED",
    database_config= DatabaseConfig(source_catalog="sandbox",
                                    source_schema="test_source",
                                    target_catalog="sandbox",
                                    target_schema="test_target"
                                    ),
    metadata_config = ReconcileMetadataConfig(
        catalog = "sandbox",
        schema= "reconcile"
    ),
    job_id="e2e_test_recon_job",
    tables=ReconcileTablesConfig("all", list(table_recon_json))
)
config = LakebridgeConfiguration(None, reconcile_config)

def test_recon(mock_workspace_client):
    ctx = ApplicationContext(mock_workspace_client)
    ctx.workspace_installation.install(config)
    recon_runner = ReconcileRunner(
        ctx.workspace_client,
        ctx.installation,
        ctx.install_state,
        ctx.prompts,
    )
    recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
