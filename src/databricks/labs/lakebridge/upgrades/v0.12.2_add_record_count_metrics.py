# pylint: disable=invalid-name
import logging

from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.helpers import db_sql

logger = logging.getLogger(__name__)


def _upgrade_reconcile_metadata_metrics_table(
    installation: Installation, ws: WorkspaceClient, app_context: ApplicationContext
):
    reconcile_config = app_context.recon_config
    assert reconcile_config, "Reconcile config must be present to upgrade the reconcile metadata metrics table"
    table_name = "metrics"
    table_identifier = (
        f"{reconcile_config.metadata_config.catalog}.{reconcile_config.metadata_config.schema}.{table_name}"
    )

    sqls: list = [
        f"ALTER TABLE {table_identifier} ADD COLUMN recon_metrics.source_record_count BIGINT",
        f"ALTER TABLE {table_identifier} ADD COLUMN recon_metrics.target_record_count BIGINT",
    ]

    for sql in sqls:
        logger.debug(f"Executing SQL to upgrade metrics table: \n{sql}")
        db_sql.get_sql_backend(ws).execute(sql)
    installation.save(reconcile_config)
    logger.debug("Upgraded Reconcile metrics table with record count fields")


def upgrade(installation: Installation, ws: WorkspaceClient):
    app_context = ApplicationContext(ws)
    if app_context.recon_config is not None:
        _upgrade_reconcile_metadata_metrics_table(installation, ws, app_context)
