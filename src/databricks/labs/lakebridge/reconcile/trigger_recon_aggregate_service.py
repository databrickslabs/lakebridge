from datetime import datetime

from pyspark.sql import SparkSession
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile import utils
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException, ReconciliationException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconIntermediatePersist,
    generate_final_reconcile_aggregate_output,
)
from databricks.labs.lakebridge.reconcile.recon_config import AGG_RECONCILE_OPERATION_NAME
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    ReconcileProcessDuration,
    AggregateQueryOutput,
    DataReconcileOutput,
)
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService


class TriggerReconAggregateService:
    @staticmethod
    def trigger_recon_aggregates(
        ws: WorkspaceClient,
        spark: SparkSession,
        table_recon: TableRecon,
        reconcile_config: ReconcileConfig,
        local_test_run: bool = False,
    ):
        reconciler, recon_capture = TriggerReconService.create_recon_dependencies(
            ws, spark, reconcile_config, local_test_run
        )

        # Get the Aggregated Reconciliation Output for each table
        for table_conf in table_recon.tables:
            recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now()), end_ts=None)
            try:
                src_schema, tgt_schema = TriggerReconService.get_schemas(
                    reconciler.source, reconciler.target, table_conf, reconcile_config.database_config, False
                )
            except DataSourceRuntimeException as e:
                raise ReconciliationException(message=str(e)) from e

            assert table_conf.aggregates, "Aggregates must be defined for Aggregates Reconciliation"

            try:
                table_reconcile_agg_output_list = reconciler.reconcile_aggregates(table_conf, src_schema, tgt_schema)
            except DataSourceRuntimeException as e:
                table_reconcile_agg_output_list = [
                    AggregateQueryOutput(reconcile_output=DataReconcileOutput(exception=str(e)), rule=None)
                ]

            recon_process_duration.end_ts = str(datetime.now())

            # Persist the data to the delta tables
            recon_capture.store_aggregates_metrics(
                reconcile_agg_output_list=table_reconcile_agg_output_list,
                table_conf=table_conf,
                recon_process_duration=recon_process_duration,
            )

            (
                ReconIntermediatePersist(
                    spark=spark,
                    path=utils.generate_volume_path(table_conf, reconcile_config.metadata_config),
                ).clean_unmatched_df_from_volume()
            )

        return TriggerReconService.verify_successful_reconciliation(
            generate_final_reconcile_aggregate_output(
                recon_id=recon_capture.recon_id,
                spark=spark,
                metadata_config=reconcile_config.metadata_config,
                local_test_run=local_test_run,
            ),
            operation_name=AGG_RECONCILE_OPERATION_NAME,
        )
