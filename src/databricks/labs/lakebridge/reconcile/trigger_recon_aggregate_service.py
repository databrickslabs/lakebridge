import logging
from datetime import datetime, timezone

from pyspark.sql import SparkSession
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.reconcile.recon_capture import (
    generate_final_reconcile_aggregate_output,
    ReconCapture,
)
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    ReconcileProcessDuration,
    AggregateQueryOutput,
    DataReconcileOutput,
    ReconcileOutput,
)
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService

logger = logging.getLogger(__name__)


class TriggerReconAggregateService:
    @staticmethod
    def trigger_recon_aggregates(
        ws: WorkspaceClient,
        spark: SparkSession,
        table_recon: TableRecon,
        reconcile_config: ReconcileConfig,
    ) -> ReconcileOutput:
        # Captured before create_recon_dependencies may pin it to UTC for a Redshift
        # source (see TriggerReconService.pin_utc_session), so it can be restored
        # once this recon completes — the mutation must not outlive the recon on a
        # shared/interactive cluster. Mirrors TriggerReconService.trigger_recon.
        original_tz: str = spark.conf.get("spark.sql.session.timeZone", "UTC") or "UTC"
        reconciler, recon_capture = TriggerReconService.create_recon_dependencies(ws, spark, reconcile_config)

        try:
            for table_conf in table_recon.tables:
                TriggerReconAggregateService.recon_aggregate_one(
                    reconciler, table_conf, reconcile_config, recon_capture
                )

            return TriggerReconService.verify_successful_reconciliation(
                generate_final_reconcile_aggregate_output(
                    recon_id=recon_capture.recon_id,
                    spark=spark,
                    metadata_config=reconcile_config.metadata_config,
                ),
                report_type="aggregate",
            )
        finally:
            TriggerReconService.finalize_recon_session(ws, spark, reconciler, original_tz)

    @staticmethod
    def recon_aggregate_one(
        reconciler: Reconciliation, table_conf: Table, reconcile_config: ReconcileConfig, recon_capture: ReconCapture
    ) -> None:
        normalized_table_conf = NormalizeReconConfigService(
            reconciler.source, reconciler.target
        ).normalize_recon_table_config(table_conf)
        if not normalized_table_conf.aggregates:
            raise ValueError("Aggregates must be defined for Aggregates Reconciliation")

        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now(tz=timezone.utc)), end_ts=None)
        try:
            src_schema, tgt_schema = TriggerReconService.get_schemas(
                reconciler.source,
                reconciler.target,
                normalized_table_conf,
                reconcile_config.source,
                reconcile_config.target,
                True,
            )

            table_reconcile_agg_output_list = reconciler.reconcile_aggregates(
                normalized_table_conf, src_schema, tgt_schema
            )
        except DataSourceRuntimeException as e:
            table_reconcile_agg_output_list = [
                AggregateQueryOutput(reconcile_output=DataReconcileOutput(exception=str(e)), rule=None)
            ]

        recon_process_duration.end_ts = str(datetime.now(tz=timezone.utc))

        recon_capture.store_aggregates_metrics(
            reconcile_agg_output_list=table_reconcile_agg_output_list,
            table_conf=normalized_table_conf,
            recon_process_duration=recon_process_duration,
        )
