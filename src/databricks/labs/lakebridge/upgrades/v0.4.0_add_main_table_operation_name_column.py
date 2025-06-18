# pylint: disable=invalid-name
import logging


from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.recon import RECON_JOB_NAME
from databricks.labs.lakebridge.helpers import db_sql

from databricks.labs.lakebridge.deployment.upgrade_common import (
    current_table_columns,
    installed_table_columns,
    recreate_table_sql,
)

logger = logging.getLogger(__name__)


def _check_table_mismatch(
    installed_table,
    current_table,
) -> bool:
    current_table = [x for x in current_table if x != "operation_name"]
    # Compare the current main table columns with the installed main table columns
    if "operation_name" in installed_table and len(sorted(installed_table)) != len(sorted(current_table)):
        return True
    return False


def _upgrade_reconcile_metadata_main_table(
    installation: Installation,
    ws: WorkspaceClient,
    app_context: ApplicationContext,
):
    """
    Add operation_name column to the main table as part of the upgrade process.
    - Compare the current main table columns with the installed main table columns. If there is any mismatch:
       * Verify all the current main table columns are present in the installed main table and then use CTAS to recreate the main table
       * If any of the current main table columns are missing in the installed main table, prompt the user to recreate the main table:
            - If the user confirms, recreate the main table using the main DDL file, else log an error message and exit
    :param installation:
    :param ws:
    :param app_context:
    """
    reconcile_config = app_context.recon_config
    assert reconcile_config, "Reconcile config must be present to upgrade the reconcile metadata main table"
    table_name = "main"
    table_identifier = (
        f"{reconcile_config.metadata_config.catalog}.{reconcile_config.metadata_config.schema}.{table_name}"
    )
    installed_columns = installed_table_columns(ws, table_identifier)
    current_columns = current_table_columns(table_name, table_identifier)
    sql: str | None = f"ALTER TABLE {table_identifier} ADD COLUMN operation_name  STRING AFTER report_type"
    if _check_table_mismatch(installed_columns, current_columns):
        logger.info("Recreating main table")
        sql = recreate_table_sql(table_identifier, installed_columns, current_columns, app_context.prompts)
    if sql:
        logger.debug(f"Executing SQL to upgrade main table: \n{sql}")
        db_sql.get_sql_backend(ws).execute(sql)
        installation.save(reconcile_config)
        logger.debug("Upgraded Reconcile main table")


def _upgrade_reconcile_workflow(app_context: ApplicationContext):
    if app_context.recon_config:
        logger.info("Upgrading reconcile workflow")
        wheels = app_context.product_info.wheels(app_context.workspace_client)
        with wheels as wheel_builder:
            wheel_path = f"/Workspace{wheel_builder.upload_to_wsfs()}"
        app_context.job_deployment.deploy_recon_job(RECON_JOB_NAME, app_context.recon_config, wheel_path)
        logger.debug("Upgraded reconcile workflow")


def upgrade(installation: Installation, ws: WorkspaceClient):
    app_context = ApplicationContext(ws)
    if app_context.recon_config is not None:
        _upgrade_reconcile_metadata_main_table(installation, ws, app_context)
        _upgrade_reconcile_workflow(app_context)
