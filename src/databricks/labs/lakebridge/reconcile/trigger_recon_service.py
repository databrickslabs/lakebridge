import logging
from datetime import datetime, timezone

from pyspark.errors import PySparkException
from pyspark.sql import SparkSession

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
    generate_final_reconcile_output,
)
from databricks.labs.lakebridge.reconcile.recon_config import Table, Schema
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    ReconcileOutput,
    ReconcileProcessDuration,
    SchemaReconcileOutput,
    DataReconcileOutput,
)
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.recon_service_helpers import (
    cleanup_intermediate_persist,
    create_recon_dependencies,
    get_schemas,
    verify_successful_reconciliation,
)
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service import TriggerReconAggregateService

logger = logging.getLogger(__name__)


class TriggerReconService:

    @staticmethod
    def trigger_recon(
        ws: WorkspaceClient,
        spark: SparkSession,
        table_recon: TableRecon,
        reconcile_config: ReconcileConfig,
    ) -> ReconcileOutput:
        # When report_type is "aggregate", forward to the dedicated aggregate
        # service. _do_recon_one only branches on {"schema","all"} and
        # {"data","row","all"}, so an unforwarded aggregate request would
        # silently no-op and return status=true without comparing anything.
        if reconcile_config.report_type.lower() == "aggregate":
            return TriggerReconAggregateService.trigger_recon_aggregates(
                ws=ws,
                spark=spark,
                table_recon=table_recon,
                reconcile_config=reconcile_config,
            )

        reconciler, recon_capture = create_recon_dependencies(ws, spark, reconcile_config)

        try:
            for table_conf in table_recon.tables:
                TriggerReconService.recon_one(reconciler, recon_capture, reconcile_config, table_conf)

            return verify_successful_reconciliation(
                generate_final_reconcile_output(
                    recon_id=recon_capture.recon_id,
                    spark=spark,
                    metadata_config=reconcile_config.metadata_config,
                ),
                reconcile_config.report_type,
            )
        finally:
            cleanup_intermediate_persist(ws, reconciler)

    @staticmethod
    def recon_one(
        reconciler: Reconciliation,
        recon_capture: ReconCapture,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
    ) -> tuple[SchemaReconcileOutput, DataReconcileOutput]:
        normalized_table_conf = NormalizeReconConfigService(
            reconciler.source, reconciler.target
        ).normalize_recon_table_config(table_conf)

        schema_reconcile_output, data_reconcile_output, recon_process_duration = TriggerReconService._do_recon_one(
            reconciler, reconcile_config, normalized_table_conf
        )

        recon_capture.start(
            data_reconcile_output=data_reconcile_output,
            schema_reconcile_output=schema_reconcile_output,
            table_conf=table_conf,
            recon_process_duration=recon_process_duration,
            record_count=reconciler.get_record_count(table_conf, reconciler.report_type),
        )

        return schema_reconcile_output, data_reconcile_output

    @staticmethod
    def _do_recon_one(reconciler: Reconciliation, reconcile_config: ReconcileConfig, table_conf: Table):
        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now(tz=timezone.utc)), end_ts=None)
        schema_reconcile_output = SchemaReconcileOutput(is_valid=True)
        data_reconcile_output = DataReconcileOutput()

        try:
            src_schema, tgt_schema = get_schemas(
                reconciler.source, reconciler.target, table_conf, reconcile_config.database_config, True
            )
        except DataSourceRuntimeException as e:
            schema_reconcile_output = SchemaReconcileOutput(is_valid=False, exception=str(e))
        else:
            if reconciler.report_type in {"schema", "all"}:
                schema_reconcile_output = TriggerReconService._run_reconcile_schema(
                    reconciler=reconciler,
                    table_conf=table_conf,
                    src_schema=src_schema,
                    tgt_schema=tgt_schema,
                )
                logger.info("Schema comparison is completed.")

            if reconciler.report_type in {"data", "row", "all"}:
                data_reconcile_output = TriggerReconService._run_reconcile_data(
                    reconciler=reconciler,
                    table_conf=table_conf,
                    src_schema=src_schema,
                    tgt_schema=tgt_schema,
                )
                logger.info(f"Reconciliation for '{reconciler.report_type}' report completed.")

        recon_process_duration.end_ts = str(datetime.now(tz=timezone.utc))
        return schema_reconcile_output, data_reconcile_output, recon_process_duration

    @staticmethod
    def _run_reconcile_schema(
        reconciler: Reconciliation,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ):
        try:
            return reconciler.reconcile_schema(table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema)
        except PySparkException as e:
            return SchemaReconcileOutput(is_valid=False, exception=str(e))

    @staticmethod
    def _run_reconcile_data(
        reconciler: Reconciliation,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
    ) -> DataReconcileOutput:
        try:
            return reconciler.reconcile_data(table_conf=table_conf, src_schema=src_schema, tgt_schema=tgt_schema)
        except DataSourceRuntimeException as e:
            return DataReconcileOutput(exception=str(e))
