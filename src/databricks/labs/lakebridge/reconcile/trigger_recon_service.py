import logging
from datetime import datetime
from uuid import uuid4

from pyspark.errors import PySparkException
from pyspark.sql import SparkSession

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon, DatabaseConfig
from databricks.labs.lakebridge.reconcile import utils
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException, ReconciliationException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
    ReconIntermediatePersist,
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
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.transpiler.execute import verify_workspace_client
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)
_RECON_REPORT_TYPES = {"schema", "data", "row", "all", "aggregate"}


class TriggerReconService:

    @staticmethod
    def trigger_recon(
        ws: WorkspaceClient,
        spark: SparkSession,
        table_recon: TableRecon,
        reconcile_config: ReconcileConfig,
        local_test_run: bool = False,
    ) -> ReconcileOutput:
        reconciler, recon_capture = TriggerReconService.create_recon_dependencies(
            ws, spark, reconcile_config, local_test_run
        )

        for table_conf in table_recon.tables:
            TriggerReconService.recon_one(spark, reconciler, recon_capture, reconcile_config, table_conf)

        return TriggerReconService.verify_successful_reconciliation(
            generate_final_reconcile_output(
                recon_id=recon_capture.recon_id,
                spark=spark,
                metadata_config=reconcile_config.metadata_config,
                local_test_run=local_test_run,
            )
        )

    @staticmethod
    def create_recon_dependencies(
        ws: WorkspaceClient, spark: SparkSession, reconcile_config: ReconcileConfig, local_test_run: bool = False
    ) -> tuple[Reconciliation, ReconCapture]:
        ws_client: WorkspaceClient = verify_workspace_client(ws)

        # validate the report type
        report_type = reconcile_config.report_type.lower()
        logger.info(f"report_type: {report_type}, data_source: {reconcile_config.data_source} ")
        utils.validate_input(report_type, _RECON_REPORT_TYPES, "Invalid report type")

        source, target = utils.initialise_data_source(
            engine=reconcile_config.data_source,
            spark=spark,
            ws=ws_client,
            secret_scope=reconcile_config.secret_scope,
        )

        recon_id = str(uuid4())
        # initialise the Reconciliation
        reconciler = Reconciliation(
            source,
            target,
            reconcile_config.database_config,
            report_type,
            SchemaCompare(spark=spark),
            get_dialect(reconcile_config.data_source),
            spark,
            metadata_config=reconcile_config.metadata_config,
        )

        recon_capture = ReconCapture(
            database_config=reconcile_config.database_config,
            recon_id=recon_id,
            report_type=report_type,
            source_dialect=get_dialect(reconcile_config.data_source),
            ws=ws_client,
            spark=spark,
            metadata_config=reconcile_config.metadata_config,
            local_test_run=local_test_run,
        )

        return reconciler, recon_capture

    @staticmethod
    def recon_one(
        spark: SparkSession,
        reconciler: Reconciliation,
        recon_capture: ReconCapture,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
    ):
        normalized_table_conf = NormalizeReconConfigService(
            reconciler.source, reconciler.target
        ).normalize_recon_table_config(table_conf)

        schema_reconcile_output, data_reconcile_output, recon_process_duration = TriggerReconService._do_recon_one(
            reconciler, reconcile_config, normalized_table_conf
        )

        TriggerReconService.persist_delta_table(
            spark,
            reconciler,
            recon_capture,
            schema_reconcile_output,
            data_reconcile_output,
            reconcile_config,
            normalized_table_conf,
            recon_process_duration,
        )

    @staticmethod
    def _do_recon_one(reconciler: Reconciliation, reconcile_config: ReconcileConfig, table_conf: Table):
        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now()), end_ts=None)
        schema_reconcile_output = SchemaReconcileOutput(is_valid=True)
        data_reconcile_output = DataReconcileOutput()

        try:
            src_schema, tgt_schema = TriggerReconService.get_schemas(
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
                logger.warning("Schema comparison is completed.")

            if reconciler.report_type in {"data", "row", "all"}:
                data_reconcile_output = TriggerReconService._run_reconcile_data(
                    reconciler=reconciler,
                    table_conf=table_conf,
                    src_schema=src_schema,
                    tgt_schema=tgt_schema,
                )
                logger.warning(f"Reconciliation for '{reconciler.report_type}' report completed.")

        recon_process_duration.end_ts = str(datetime.now())
        return schema_reconcile_output, data_reconcile_output, recon_process_duration

    @staticmethod
    def get_schemas(
        source: DataSource,
        target: DataSource,
        table_conf: Table,
        database_config: DatabaseConfig,
        normalize: bool,
    ) -> tuple[list[Schema], list[Schema]]:
        src_schema = source.get_schema(
            catalog=database_config.source_catalog,
            schema=database_config.source_schema,
            table=table_conf.source_name,
            normalize=normalize,
        )

        tgt_schema = target.get_schema(
            catalog=database_config.target_catalog,
            schema=database_config.target_schema,
            table=table_conf.target_name,
            normalize=normalize,
        )

        return src_schema, tgt_schema

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

    @staticmethod
    def persist_delta_table(
        spark: SparkSession,
        reconciler: Reconciliation,
        recon_capture: ReconCapture,
        schema_reconcile_output: SchemaReconcileOutput,
        data_reconcile_output: DataReconcileOutput,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
        recon_process_duration: ReconcileProcessDuration,
    ):
        recon_capture.start(
            data_reconcile_output=data_reconcile_output,
            schema_reconcile_output=schema_reconcile_output,
            table_conf=table_conf,
            recon_process_duration=recon_process_duration,
            record_count=reconciler.get_record_count(table_conf, reconciler.report_type),
        )
        if reconciler.report_type != "schema":
            ReconIntermediatePersist(
                spark=spark, path=utils.generate_volume_path(table_conf, reconcile_config.metadata_config)
            ).clean_unmatched_df_from_volume()

    @staticmethod
    def verify_successful_reconciliation(
        reconcile_output: ReconcileOutput, operation_name: str = "reconcile"
    ) -> ReconcileOutput:
        for table_output in reconcile_output.results:
            if table_output.exception_message or (
                table_output.status.column is False
                or table_output.status.row is False
                or table_output.status.schema is False
                or table_output.status.aggregate is False
            ):
                raise ReconciliationException(
                    f" Reconciliation failed for one or more tables. Please check the recon metrics for more details."
                    f" **{operation_name}** failed.",
                    reconcile_output=reconcile_output,
                )

        logger.info("Reconciliation completed successfully.")
        return reconcile_output
