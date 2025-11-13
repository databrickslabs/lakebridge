from databricks.labs.lakebridge.cli import configure_reconcile
from databricks.labs.lakebridge.config import ReconcileConfig, DatabaseConfig, ReconcileMetadataConfig, TableRecon, \
    LakebridgeConfiguration, ReconcileTablesConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.install import installer
from databricks.labs.lakebridge.reconcile.recon_config import Table, RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from databricks.sdk import WorkspaceClient
from databricks.labs.lakebridge.__about__ import __version__
import json

table_recon = TableRecon(
    source_schema="test_source",
    target_catalog="sandbox",
    target_schema="test_target",
    tables=[
        Table(
            source_name="diamonds",
            target_name="diamonds",
            join_columns= ["color", "clarity"],
        )
    ]
)
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
    tables=ReconcileTablesConfig("all", list(json.dumps(table_recon)))
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
