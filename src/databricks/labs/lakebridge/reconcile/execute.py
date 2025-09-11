import logging
import os
import sys

from databricks.connect import DatabricksSession
from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import (
    TableRecon,
    ReconcileConfig,
)
from databricks.labs.lakebridge.reconcile.exception import (
    ReconciliationException,
)
from databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service import TriggerReconAggregateService
from databricks.labs.lakebridge.reconcile.recon_config import (
    RECONCILE_OPERATION_NAME,
    AGG_RECONCILE_OPERATION_NAME,
)
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService

logger = logging.getLogger(__name__)


def main(*argv) -> None:
    logger.debug(f"Arguments received: {argv}")

    assert len(sys.argv) == 2, f"Invalid number of arguments: {len(sys.argv)}," f" Operation name must be specified."
    operation_name = sys.argv[1]

    assert operation_name in {
        RECONCILE_OPERATION_NAME,
        AGG_RECONCILE_OPERATION_NAME,
    }, f"Invalid option: {operation_name}"

    w = WorkspaceClient()

    installation = Installation.assume_user_home(w, "lakebridge")

    reconcile_config = installation.load(ReconcileConfig)

    catalog_or_schema = (
        reconcile_config.database_config.source_catalog
        if reconcile_config.database_config.source_catalog
        else reconcile_config.database_config.source_schema
    )
    filename = f"recon_config_{reconcile_config.data_source}_{catalog_or_schema}_{reconcile_config.report_type}.json"

    logger.info(f"Loading {filename} from Databricks Workspace...")

    table_recon = installation.load(type_ref=TableRecon, filename=filename)

    if operation_name == AGG_RECONCILE_OPERATION_NAME:
        return _trigger_reconcile_aggregates(w, table_recon, reconcile_config)

    return _trigger_recon(w, table_recon, reconcile_config)


def _trigger_recon(
    w: WorkspaceClient,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
):
    try:
        recon_output = TriggerReconService.trigger_recon(
            ws=w,
            spark=DatabricksSession.builder.getOrCreate(),
            table_recon=table_recon,
            reconcile_config=reconcile_config,
        )
        logger.info(f"recon_output: {recon_output}")
        logger.info(f"recon_id: {recon_output.recon_id}")
    except ReconciliationException as e:
        logger.error(f"Error while running recon: {e.reconcile_output}")
        raise e


def _trigger_reconcile_aggregates(
    ws: WorkspaceClient,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
):
    """
    Triggers the reconciliation process for aggregated data  between source and target tables.
    Supported Aggregate functions: MIN, MAX, COUNT, SUM, AVG, MEAN, MODE, PERCENTILE, STDDEV, VARIANCE, MEDIAN

    This function attempts to reconcile aggregate data based on the configurations provided. It logs the outcome
    of the reconciliation process, including any errors encountered during execution.

    Parameters:
    - ws (WorkspaceClient): The workspace client used to interact with Databricks workspaces.
    - table_recon (TableRecon): Configuration for the table reconciliation process, including source and target details.
    - reconcile_config (ReconcileConfig): General configuration for the reconciliation process,
                                                                    including database and table settings.

    Raises:
    - ReconciliationException: If an error occurs during the reconciliation process, it is caught and re-raised
      after logging the error details.
    """
    try:
        reconcile_config.report_type = "aggregate"
        recon_output = TriggerReconAggregateService.trigger_recon_aggregates(
            ws=ws,
            spark=DatabricksSession.builder.getOrCreate(),
            table_recon=table_recon,
            reconcile_config=reconcile_config,
        )
        logger.info(f"recon_output: {recon_output}")
        logger.info(f"recon_id: {recon_output.recon_id}")
    except ReconciliationException as e:
        logger.error(f"Error while running aggregate reconcile: {str(e)}")
        raise e


if __name__ == "__main__":
    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        raise SystemExit("Only intended to run in Databricks Runtime")
    main(*sys.argv)
