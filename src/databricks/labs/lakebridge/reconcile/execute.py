import logging
import os
import sys

from databricks.connect import DatabricksSession
from databricks.labs.blueprint.installation import Installation
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.execute import (
    auto_configure_tables,
    discover_tables,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    AGG_RECONCILE_OPERATION_NAME,
    AUTO_CONFIGURE_TABLES_OPERATION_NAME,
    SUPPORTED_OPERATIONS,
    DISCOVER_TABLES_OPERATION_NAME,
    DISCOVER_AND_AUTO_CONFIGURE_TABLES_OPERATION_NAME,
)
from databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service import TriggerReconAggregateService
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService

logger = logging.getLogger(__name__)


def main(*argv: str) -> None:
    """Lakeview Jobs task entry point: reconcile"""
    initialize_logging()

    logger.debug(f"Arguments received: {argv}")
    w = WorkspaceClient()
    installation: Installation | None = None
    operation_name: str | None = None
    # sys.arg is used when running the script as an entry point which is how we trigger the job.
    args = argv[1:] if argv else tuple(sys.argv[1:])

    match args:
        case [operation_name, install_folder] if operation_name in SUPPORTED_OPERATIONS:
            installation = Installation(w, "lakebridge", install_folder=install_folder)
        case [operation_name] if operation_name in SUPPORTED_OPERATIONS:
            installation = Installation.assume_user_home(w, "lakebridge")
        case _:
            raise ValueError(
                f"Invalid arguments: {args}. Expected [operation_name, install_folder] "
                f"where operation_name is one of: {sorted(SUPPORTED_OPERATIONS)!r}."
            )

    reconcile_config = installation.load(ReconcileConfig)

    if operation_name in (
        DISCOVER_TABLES_OPERATION_NAME,
        DISCOVER_AND_AUTO_CONFIGURE_TABLES_OPERATION_NAME,
        AUTO_CONFIGURE_TABLES_OPERATION_NAME,
    ):
        _autoconfigure_tables(installation, reconcile_config, operation_name)
        return None

    filename = reconcile_config.table_recon_filename
    logger.info(f"Loading {filename} from Databricks Workspace...")

    table_recon = installation.load(type_ref=TableRecon, filename=filename)

    if operation_name == AGG_RECONCILE_OPERATION_NAME:
        return _trigger_reconcile_aggregates(w, table_recon, reconcile_config)

    return _trigger_recon(w, table_recon, reconcile_config)


def _autoconfigure_tables(installation: Installation, reconcile_config: ReconcileConfig, operation_name: str):
    spark = DatabricksSession.builder.getOrCreate()

    if operation_name == DISCOVER_TABLES_OPERATION_NAME:
        discover_tables(reconcile_config=reconcile_config, spark=spark, installation=installation)
        return

    if operation_name == AUTO_CONFIGURE_TABLES_OPERATION_NAME:
        table_recon = installation.load(type_ref=TableRecon, filename=reconcile_config.table_recon_filename)
    else:  # DISCOVER_AND_AUTO_CONFIGURE_TABLES_OPERATION_NAME
        table_recon = discover_tables(reconcile_config=reconcile_config, spark=spark, installation=installation)
    auto_configure_tables(table_recon, reconcile_config=reconcile_config, spark=spark, installation=installation)


def _trigger_recon(
    w: WorkspaceClient,
    table_recon: TableRecon,
    reconcile_config: ReconcileConfig,
):
    recon_output = TriggerReconService.trigger_recon(
        ws=w,
        spark=DatabricksSession.builder.getOrCreate(),
        table_recon=table_recon,
        reconcile_config=reconcile_config,
    )
    logger.info(f"Output: {recon_output}")


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
    reconcile_config.report_type = "aggregate"
    recon_output = TriggerReconAggregateService.trigger_recon_aggregates(
        ws=ws,
        spark=DatabricksSession.builder.getOrCreate(),
        table_recon=table_recon,
        reconcile_config=reconcile_config,
    )
    logger.info(f"Output: {recon_output}")


if __name__ == "__main__":
    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        raise SystemExit("Only intended to run in Databricks Runtime")
    main(*sys.argv)
