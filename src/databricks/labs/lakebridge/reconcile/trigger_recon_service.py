"""Reconcile orchestration.

Row-level compare path: schema compare, then — when ``fingerprint_precheck`` is
enabled and ``source.dialect`` has a registered ``FingerprintQueryBuilder``
(today: Redshift) — try fingerprint (MD5 buckets) first. MATCH returns a
synthetic match without hash + JOIN; MISMATCH builds the output from the
already-fetched filtered rows; failure or unsupported sources fall through to
``HashQueryBuilder`` + ``reconciler.reconcile_data``.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from pyspark.errors import PySparkException
from pyspark.sql import SparkSession

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon, DatabaseConfig
from databricks.labs.lakebridge.reconcile import utils
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException, ReconciliationException
from databricks.labs.lakebridge.reconcile.fingerprint.exceptions import (
    FingerprintError,
    UnmappedTargetColumnMappingError,
)
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import (
    FingerprintRunMetadata,
    INELIGIBLE_UNMAPPED_TARGET_COLUMN_MAPPING,
)
from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import (
    FingerprintResult,
    build_mismatch_output,
    classify_ineligibility,
    fingerprint_match_output,
    resolve_compare_key_columns,
    run_fingerprint_precheck,
)
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconCapture,
    generate_final_reconcile_output,
    ReconIntermediatePersist,
)
from databricks.labs.lakebridge.reconcile.recon_config import Table, Schema
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    ReconcileOutput,
    ReconcileProcessDuration,
    SchemaReconcileOutput,
    DataReconcileOutput,
    ReconcileTableOutput,
)
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.transpiler.execute import verify_workspace_client
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

logger = logging.getLogger(__name__)
_RECON_REPORT_TYPES = {"schema", "data", "row", "all", "aggregate"}


def _try_unpersist(df) -> None:
    """Best-effort ``unpersist`` for cached fingerprint frames on a fallback
    exit. The DataFrames may have been ``persist()``-ed by
    ``compute_target_fingerprint`` / source-side fetch and we don't want them
    to linger in executor storage for the rest of the recon.

    Swallows any exception because unpersist is purely a release path — failing
    here on a frame that was never cached, or whose plan is in a partial state,
    must not mask the original error that triggered the fallback.
    """
    if df is None:
        return
    try:
        df.unpersist(blocking=False)
    except Exception:  # pylint: disable=broad-except
        # Intentional: ``unpersist`` on a non-cached frame raises in some Spark
        # versions and we explicitly do not want to surface that on the fallback.
        logger.debug("Best-effort unpersist on fingerprint fallback DataFrame failed; ignoring.", exc_info=True)


class TriggerReconService:

    @staticmethod
    def trigger_recon(
        ws: WorkspaceClient,
        spark: SparkSession,
        table_recon: TableRecon,
        reconcile_config: ReconcileConfig,
    ) -> ReconcileOutput:
        reconciler, recon_capture = TriggerReconService.create_recon_dependencies(ws, spark, reconcile_config)

        try:
            for table_conf in table_recon.tables:
                TriggerReconService.recon_one(reconciler, recon_capture, reconcile_config, table_conf)

            return TriggerReconService.verify_successful_reconciliation(
                generate_final_reconcile_output(
                    recon_id=recon_capture.recon_id,
                    spark=spark,
                    metadata_config=reconcile_config.metadata_config,
                ),
                reconcile_config.report_type,
            )
        finally:
            try:
                ws.dbfs.delete(str(reconciler.intermediate_persist.base_dir), recursive=True)
            except IOError:
                logger.exception("Cleaning intermediate storage failed. Resuming program")

    @staticmethod
    def create_recon_dependencies(
        ws: WorkspaceClient, spark: SparkSession, reconcile_config: ReconcileConfig
    ) -> tuple[Reconciliation, ReconCapture]:
        ws_client: WorkspaceClient = verify_workspace_client(ws)

        # validate the report type
        report_type = reconcile_config.report_type.lower()
        source_dialect = reconcile_config.source.dialect
        logger.info(f"report_type: {report_type}, data_source: {source_dialect} ")
        utils.validate_input(report_type, _RECON_REPORT_TYPES, "Invalid report type")

        # Warn on silently-ignored knob combinations — these flags have zero
        # effect when ``fingerprint_precheck`` itself is off, so a user who has
        # flipped only a secondary knob is asking for a behaviour change they
        # will not get. Surfacing this once per recon is enough to catch the
        # typo without spamming per-table.
        if reconcile_config.fingerprint_treat_empty_as_null and not reconcile_config.fingerprint_precheck:
            logger.warning(
                "ReconcileConfig.fingerprint_treat_empty_as_null is True but "
                "fingerprint_precheck is False; the empty-as-null behaviour "
                "applies only to the fingerprint hash path, so this knob is "
                "ignored. Enable fingerprint_precheck to take effect."
            )
        if reconcile_config.fingerprint_row_count_override is not None and not reconcile_config.fingerprint_precheck:
            logger.warning(
                "ReconcileConfig.fingerprint_row_count_override is set but "
                "fingerprint_precheck is False; the override drives only the "
                "fingerprint sub-bucket tier, so this knob is ignored."
            )

        # validate the connection
        source, target = utils.initialise_data_source(
            source_dialect=reconcile_config.source.dialect,
            spark=spark,
            connection_name=reconcile_config.source.uc_connection_name,
        )

        recon_id = uuid4().hex
        # initialise the Reconciliation
        reconciler = Reconciliation(
            source,
            target,
            reconcile_config.database_config,
            report_type,
            SchemaCompare(spark=spark),
            get_dialect(source_dialect),
            spark,
            metadata_config=reconcile_config.metadata_config,
            intermediate_persist=ReconIntermediatePersist(spark, reconcile_config.metadata_config),
        )

        recon_capture = ReconCapture(
            database_config=reconcile_config.database_config,
            recon_id=recon_id,
            report_type=report_type,
            source_dialect=get_dialect(source_dialect),
            ws=ws_client,
            spark=spark,
            metadata_config=reconcile_config.metadata_config,
        )

        return reconciler, recon_capture

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

        (
            schema_reconcile_output,
            data_reconcile_output,
            recon_process_duration,
            fingerprint_metadata,
        ) = TriggerReconService._do_recon_one(
            reconciler, reconcile_config, normalized_table_conf, recon_id=recon_capture.recon_id
        )

        recon_capture.start(
            data_reconcile_output=data_reconcile_output,
            schema_reconcile_output=schema_reconcile_output,
            table_conf=table_conf,
            recon_process_duration=recon_process_duration,
            record_count=reconciler.get_record_count(table_conf, reconciler.report_type),
            fingerprint_metadata=fingerprint_metadata,
        )

        return schema_reconcile_output, data_reconcile_output

    @staticmethod
    def _do_recon_one(
        reconciler: Reconciliation,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
        *,
        recon_id: str | None = None,
    ):
        recon_process_duration = ReconcileProcessDuration(start_ts=str(datetime.now(tz=timezone.utc)), end_ts=None)
        schema_reconcile_output = SchemaReconcileOutput(is_valid=True)
        data_reconcile_output = DataReconcileOutput()

        # Compute ineligibility once so metadata is populated correctly
        # regardless of which code path exits first. The data-path block
        # below overwrites the eligible default with the actual verdict.
        ineligibility_reason = classify_ineligibility(
            flag_enabled=reconcile_config.fingerprint_precheck,
            data_source=reconcile_config.source.dialect,
            report_type=reconciler.report_type,
            table_conf=table_conf,
        )
        if ineligibility_reason is not None:
            fingerprint_metadata: FingerprintRunMetadata = FingerprintRunMetadata.ineligible(ineligibility_reason)
        else:
            fingerprint_metadata = FingerprintRunMetadata(eligible=True)

        try:
            src_schema, tgt_schema = TriggerReconService.get_schemas(
                reconciler.source, reconciler.target, table_conf, reconcile_config.database_config, True
            )
        except DataSourceRuntimeException as e:
            schema_reconcile_output = SchemaReconcileOutput(is_valid=False, exception=str(e))
            if ineligibility_reason is None:
                fingerprint_metadata = FingerprintRunMetadata(
                    eligible=True, fallback_to_full_pipeline=True, verdict="FAILED"
                )
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
                data_reconcile_output, fingerprint_metadata = TriggerReconService._run_fingerprint_or_reconcile_data(
                    reconciler=reconciler,
                    reconcile_config=reconcile_config,
                    table_conf=table_conf,
                    src_schema=src_schema,
                    tgt_schema=tgt_schema,
                    ineligibility_reason=ineligibility_reason,
                    recon_id=recon_id,
                )
                logger.info(f"Reconciliation for '{reconciler.report_type}' report completed.")

        recon_process_duration.end_ts = str(datetime.now(tz=timezone.utc))
        return schema_reconcile_output, data_reconcile_output, recon_process_duration, fingerprint_metadata

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
    def _invoke_precheck(
        *,
        reconciler: Reconciliation,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
        recon_id: str | None,
    ) -> tuple[FingerprintResult | None, str | None, bool]:
        """Run ``run_fingerprint_precheck`` and classify the outcome for the caller.

        Returns ``(fp_result, runtime_ineligibility, precheck_failed)`` where:

        * ``fp_result`` is the ``FingerprintResult`` (or ``None`` if the precheck
          declined / raised),
        * ``runtime_ineligibility`` is an ``IneligibilityReason`` value when the
          precheck was rejected for a *config-time* reason discovered at runtime
          (today: ``UnmappedTargetColumnMappingError``). The caller routes this
          through ``FingerprintRunMetadata.ineligible(...)`` so adoption queries
          on ``recon_metrics.fingerprint_metrics.ineligibility_reason`` see the
          typed value instead of a silent ``None``.
        * ``precheck_failed`` is ``True`` when the precheck raised a runtime
          fault (``FingerprintError`` / ``DataSourceRuntimeException`` /
          ``PySparkException``). The caller maps that to ``verdict="FAILED"`` so
          dashboards can quantify precheck reliability.

        Extracting this keeps the parent method's branching surface small
        enough for the project's McCabe budget while preserving every catch.
        """
        try:
            fp_result = run_fingerprint_precheck(
                source=reconciler.source,
                target=reconciler.target,
                spark=reconciler.spark,
                source_engine=reconciler.source_engine,
                database_config=reconcile_config.database_config,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
                report_type=reconciler.report_type,
                data_source=reconcile_config.source.dialect,
                treat_empty_as_null=reconcile_config.fingerprint_treat_empty_as_null,
                target_row_count_override=reconcile_config.fingerprint_row_count_override,
                recon_id=recon_id,
            )
        except UnmappedTargetColumnMappingError as e:
            # Caught BEFORE the generic ``FingerprintError`` branch because this
            # is a config-time ineligibility (a column_mapping target that
            # doesn't exist on the target), not a runtime failure of the
            # precheck. Surfacing it as a typed ``ineligibility_reason`` keeps
            # adoption queries honest.
            logger.warning(f"Fingerprint precheck ineligible — {e}; falling back to full pipeline.")
            return None, INELIGIBLE_UNMAPPED_TARGET_COLUMN_MAPPING, False
        except (FingerprintError, DataSourceRuntimeException, PySparkException) as e:
            # Three failure modes meet here:
            #   * ``FingerprintError`` — logical errors raised by the precheck.
            #   * ``DataSourceRuntimeException`` — wrapped JDBC failures from
            #     the connector layer during detection or fetch.
            #   * ``PySparkException`` — bare Spark errors that the connector
            #     wrap doesn't see, e.g. ``AnalysisException`` raised at action
            #     time when ``compute_target_fingerprint`` materialises a plan
            #     that references a missing target column. Without this catch
            #     the recon would crash mid-pipeline instead of falling back;
            #     the precheck must be opt-in safe.
            # All three collapse to "fallback to full pipeline with verdict=FAILED"
            # so dashboards can quantify precheck reliability without distinguishing
            # the cause — the underlying error is logged here and re-discoverable
            # from cluster logs if needed.
            logger.warning(f"Fingerprint precheck failed ({e}); falling back to full pipeline.")
            return None, None, True
        return fp_result, None, False

    @staticmethod
    def _run_fingerprint_or_reconcile_data(
        reconciler: Reconciliation,
        reconcile_config: ReconcileConfig,
        table_conf: Table,
        src_schema: list[Schema],
        tgt_schema: list[Schema],
        ineligibility_reason: str | None = None,
        recon_id: str | None = None,
    ) -> tuple[DataReconcileOutput, FingerprintRunMetadata]:
        """Try the fingerprint precheck; on any non-MATCH outcome, fall back to the
        full hash-and-join reconcile path.

        Returns ``(data_reconcile_output, fingerprint_metadata)``. The metadata
        records the verdict regardless of which path produced the output, so
        the persisted ``recon_metrics.fingerprint_metrics`` struct always
        reflects what actually happened.

        ``ineligibility_reason`` may be supplied by the caller (``_do_recon_one``
        pre-computes it once so the schema-failure path can pre-populate the
        metadata) but is computed lazily here when omitted, so this helper is
        usable as a standalone unit-test boundary.
        """
        if ineligibility_reason is None:
            ineligibility_reason = classify_ineligibility(
                flag_enabled=reconcile_config.fingerprint_precheck,
                data_source=reconcile_config.source.dialect,
                report_type=reconciler.report_type,
                table_conf=table_conf,
            )

        if ineligibility_reason is not None:
            data_reconcile_output = TriggerReconService._run_reconcile_data(
                reconciler=reconciler,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
            )
            return data_reconcile_output, FingerprintRunMetadata.ineligible(ineligibility_reason)

        fp_result, runtime_ineligibility, precheck_failed = TriggerReconService._invoke_precheck(
            reconciler=reconciler,
            reconcile_config=reconcile_config,
            table_conf=table_conf,
            src_schema=src_schema,
            tgt_schema=tgt_schema,
            recon_id=recon_id,
        )

        if runtime_ineligibility is not None:
            data_reconcile_output = TriggerReconService._run_reconcile_data(
                reconciler=reconciler,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
            )
            return data_reconcile_output, FingerprintRunMetadata.ineligible(runtime_ineligibility)

        if fp_result is None:
            # ``None`` covers two cases:
            #   - the precheck raised (precheck_failed=True) → verdict="FAILED"
            #   - the precheck declined (column-resolution skip, systemic
            #     mismatch, no solved buckets) → verdict left unset
            # In both cases the full pipeline produces the answer and the
            # metadata records a fallback.
            data_reconcile_output = TriggerReconService._run_reconcile_data(
                reconciler=reconciler,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
            )
            return data_reconcile_output, FingerprintRunMetadata.fallback(
                verdict="FAILED" if precheck_failed else None,
            )

        if fp_result.verdict == "MATCH":
            return fingerprint_match_output(), FingerprintRunMetadata.from_result(fp_result, verdict="MATCH")

        # MISMATCH: the precheck has fetched the differing rows. If the rows
        # are missing (e.g. an upstream codepath returned ``MISMATCH`` without
        # populating both row sets), we cannot build the output here and must
        # fall back to the full pipeline — preserving solver counters so the
        # dashboard still shows what the precheck observed. Release any cached
        # frames the precheck may have left behind so the executor's storage
        # layer doesn't carry the dead plan through the rest of the recon.
        if fp_result.source_rows is None or fp_result.target_rows is None:
            _try_unpersist(fp_result.source_rows)
            _try_unpersist(fp_result.target_rows)
            data_reconcile_output = TriggerReconService._run_reconcile_data(
                reconciler=reconciler,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
            )
            return data_reconcile_output, FingerprintRunMetadata.from_result(
                fp_result, verdict="MISMATCH", fallback_to_full_pipeline=True
            )

        try:
            data_reconcile_output = build_mismatch_output(
                src_hashed=fp_result.source_rows,
                tgt_hashed=fp_result.target_rows,
                key_columns=resolve_compare_key_columns(table_conf),
                report_type=reconciler.report_type,
                persistence=reconciler.intermediate_persist,
            )
        except (DataSourceRuntimeException, PySparkException) as e:
            # ``build_mismatch_output`` runs Spark actions on the prefetched src/tgt
            # frames; an analysis or runtime failure here must not crash the recon.
            # Mirror the fail-open pattern used by every other non-MATCH branch in
            # this method: fall through to the standard full pipeline so the table
            # still gets a real recon answer, and record on the metadata that the
            # precheck-built output was rejected. Release the cached frames first
            # so a partial materialisation does not linger in executor storage for
            # the full recon lifetime.
            _try_unpersist(fp_result.source_rows)
            _try_unpersist(fp_result.target_rows)
            logger.warning(f"Fingerprint mismatch-output build failed ({e}); falling back to full pipeline.")
            data_reconcile_output = TriggerReconService._run_reconcile_data(
                reconciler=reconciler,
                table_conf=table_conf,
                src_schema=src_schema,
                tgt_schema=tgt_schema,
            )
            return data_reconcile_output, FingerprintRunMetadata.from_result(
                fp_result, verdict="MISMATCH", fallback_to_full_pipeline=True
            )
        return data_reconcile_output, FingerprintRunMetadata.from_result(fp_result, verdict="MISMATCH")

    @staticmethod
    def verify_successful_reconciliation(reconcile_output: ReconcileOutput, report_type: str) -> ReconcileOutput:
        def is_table_recon_mismatch(table_output: ReconcileTableOutput):
            is_mismatch = (
                table_output.status.column is False
                or table_output.status.row is False
                or table_output.status.schema is False
                or table_output.status.aggregate is False
            )
            if is_mismatch:
                logger.debug(
                    f"Mismatches found between source and target tables:"
                    f" ({table_output.source_table_name}, {table_output.target_table_name})."
                )

            return is_mismatch

        exceptions = [r for r in reconcile_output.results if r.exception_message]
        mismatched = [r for r in reconcile_output.results if is_table_recon_mismatch(r)]

        total_count, exc_count, mismatched_count = (len(reconcile_output.results), len(exceptions), len(mismatched))
        success_count = max(0, total_count - exc_count + mismatched_count)

        logger.info(
            f"Reconciliation **{report_type}** with id: {reconcile_output.recon_id} ran for total {total_count} source tables and their targets."
            f" {success_count} tables succeeded, {exc_count} tables failed with exceptions and {mismatched_count} tables mismatched."
        )

        if exceptions:
            raise ReconciliationException(
                f"Reconciliation **{report_type}** with id: {reconcile_output.recon_id} failed with exceptions for {exc_count} table(s). Please check recon metrics for details.",
                reconcile_output=reconcile_output,
            )

        if mismatched:
            logger.error(
                f"Reconciliation **{report_type}** with id: {reconcile_output.recon_id} found mismatches in {mismatched_count} table(s). Please check recon metrics for details."
            )
        else:
            logger.info(
                f"Reconciliation **{report_type}** with id: {reconcile_output.recon_id} completed successfully. Please check recon metrics for details."
            )

        return reconcile_output
