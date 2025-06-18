# pylint: disable=invalid-name
import logging

from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.deployment.upgrade_common import (
    current_table_columns,
    installed_table_columns,
    check_table_mismatch,
    recreate_table_sql,
)
from databricks.labs.lakebridge.helpers import db_sql

logger = logging.getLogger(__name__)


def _upgrade_reconcile_metadata_metrics_table(
    installation: Installation, ws: WorkspaceClient, app_context: ApplicationContext
):
    reconcile_config = app_context.recon_config
    assert reconcile_config, "Reconcile config must be present to upgrade the reconcile metadata main table"
    table_name = "metrics"
    table_identifier = (
        f"{reconcile_config.metadata_config.catalog}.{reconcile_config.metadata_config.schema}.{table_name}"
    )
    installed_columns = installed_table_columns(ws, table_identifier)
    current_columns = current_table_columns(table_name, table_identifier)
    sqls: list | None = [
        f"ALTER TABLE {table_identifier} SET TBLPROPERTIES ('delta.enableTypeWidening' = 'true')",
        f"ALTER TABLE {table_identifier} ALTER COLUMN recon_metrics.row_comparison.missing_in_source TYPE BIGINT",
        f"ALTER TABLE {table_identifier} ALTER COLUMN recon_metrics.row_comparison.missing_in_target TYPE BIGINT",
        f"ALTER TABLE {table_identifier} ALTER COLUMN recon_metrics.column_comparison.absolute_mismatch TYPE BIGINT",
        f"ALTER TABLE {table_identifier} ALTER COLUMN recon_metrics.column_comparison.threshold_mismatch TYPE BIGINT",
    ]
    if check_table_mismatch(installed_columns, current_columns):
        logger.info("Recreating main table")
        sqls = [recreate_table_sql(table_identifier, installed_columns, current_columns, app_context.prompts)]
    if sqls:
        for sql in sqls:
            logger.debug(f"Executing SQL to upgrade metrics table: \n{sql}")
            db_sql.get_sql_backend(ws).execute(sql)
        installation.save(reconcile_config)
        logger.debug("Upgraded Reconcile metrics table")


def upgrade(installation: Installation, ws: WorkspaceClient):
    app_context = ApplicationContext(ws)
    if app_context.recon_config is not None:
        _upgrade_reconcile_metadata_metrics_table(installation, ws, app_context)
