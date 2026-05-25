"""Unit tests for the FingerprintRunMetadata produced by the trigger service.

Covers every branch of ``_run_fingerprint_or_reconcile_data``: ineligible / MATCH /
MISMATCH-with-rows / MISMATCH-fallback / soft-skip / FAILED. Tests stub at
``run_fingerprint_precheck`` and ``_run_reconcile_data``, so no SparkSession needed.
"""

from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.exception import DataSourceRuntimeException
from databricks.labs.lakebridge.reconcile.fingerprint.exceptions import UnmappedTargetColumnMappingError
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import (
    INELIGIBLE_FILTERS_CONFIGURED,
    INELIGIBLE_FLAG_DISABLED,
    INELIGIBLE_NO_JOIN_COLUMNS,
    INELIGIBLE_UNMAPPED_TARGET_COLUMN_MAPPING,
    INELIGIBLE_UNSUPPORTED_DIALECT,
)
from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import FingerprintResult
from databricks.labs.lakebridge.reconcile.recon_config import Filters, Table
from databricks.labs.lakebridge.reconcile.recon_output_config import DataReconcileOutput, MismatchOutput
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService


def _reconciler(report_type: str = "data") -> MagicMock:
    """Smallest Reconciliation mock the trigger reads from."""
    reconciler = MagicMock()
    reconciler.report_type = report_type
    reconciler.source = MagicMock()
    reconciler.target = MagicMock()
    reconciler.spark = MagicMock()
    reconciler.source_engine = MagicMock()
    reconciler.intermediate_persist = MagicMock()
    return reconciler


def _config(*, flag: bool = True, source: str = "redshift") -> ReconcileConfig:
    return ReconcileConfig(
        report_type="data",
        source=SourceConnectionConfig(
            dialect=source,
            catalog="dev",
            schema="src",
            uc_connection_name="conn",
        ),
        target=TargetConnectionConfig(catalog="tc", schema="ts"),
        metadata_config=MagicMock(),
        fingerprint_precheck=flag,
    )


def _table(**overrides) -> Table:
    base = {"source_name": "t", "target_name": "t", "join_columns": ["id"]}
    base.update(overrides)
    return Table(**base)  # type: ignore[arg-type]


def _stub_full_pipeline_output() -> DataReconcileOutput:
    """Sentinel mismatch_count proves the trigger returned the full-pipeline output
    (not the zeroed ``fingerprint_match_output()``).
    """
    return DataReconcileOutput(mismatch_count=999, mismatch=MismatchOutput())


@patch.object(TriggerReconService, "_run_reconcile_data")
def test_flag_disabled_records_flag_disabled_reason(mock_full):
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(flag=False),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is False
    assert metadata.ineligibility_reason == INELIGIBLE_FLAG_DISABLED
    assert metadata.fallback_to_full_pipeline is False, (
        "Ineligible tables didn't *fall back* — they were never eligible to begin with. "
        "Conflating these would inflate fallback rates in dashboards."
    )
    assert output.mismatch_count == 999
    mock_full.assert_called_once()


@patch.object(TriggerReconService, "_run_reconcile_data")
def test_unsupported_dialect_records_unsupported_reason(mock_full):
    mock_full.return_value = _stub_full_pipeline_output()

    _, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(source="snowflake"),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.ineligibility_reason == INELIGIBLE_UNSUPPORTED_DIALECT
    mock_full.assert_called_once()


@patch.object(TriggerReconService, "_run_reconcile_data")
def test_no_join_columns_records_no_join_columns_reason(mock_full):
    mock_full.return_value = _stub_full_pipeline_output()

    _, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=Table(source_name="t", target_name="t"),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.ineligibility_reason == INELIGIBLE_NO_JOIN_COLUMNS


@patch.object(TriggerReconService, "_run_reconcile_data")
def test_per_table_filter_records_filters_reason(mock_full):
    mock_full.return_value = _stub_full_pipeline_output()

    _, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(filters=Filters(source="x is not null")),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.ineligibility_reason == INELIGIBLE_FILTERS_CONFIGURED


@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_match_records_match_verdict_with_elapsed(mock_precheck):
    """MATCH must short-circuit the full pipeline AND record verdict + elapsed time.

    The verdict must come from the FingerprintResult (MATCH), not be hard-coded —
    otherwise the dashboard can't differentiate MATCH from MISMATCH.
    """
    mock_precheck.return_value = FingerprintResult(verdict="MATCH", detection_elapsed_ms=137)

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict == "MATCH"
    assert metadata.elapsed_ms == 137
    assert metadata.fallback_to_full_pipeline is False
    # MATCH path returns the synthetic match output, not the full-pipeline sentinel.
    assert output.mismatch_count == 0
    assert output.missing_in_src_count == 0
    assert output.missing_in_tgt_count == 0


@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.build_mismatch_output")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_mismatch_with_rows_records_solver_counters(mock_precheck, mock_build):
    """MISMATCH with both row sets — solver counters and elapsed must be persisted."""
    src_rows = MagicMock()
    src_rows.count.return_value = 5
    tgt_rows = MagicMock()
    tgt_rows.count.return_value = 3
    mock_precheck.return_value = FingerprintResult(
        verdict="MISMATCH",
        source_rows=src_rows,
        target_rows=tgt_rows,
        solved_count=4,
        unsolved_sb_count=2,
        total_mismatched_sbs=6,
        detection_elapsed_ms=99,
    )
    mock_build.return_value = DataReconcileOutput(mismatch_count=4, mismatch=MismatchOutput())

    _, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict == "MISMATCH"
    assert metadata.elapsed_ms == 99
    assert metadata.solved_count == 4
    assert metadata.unsolved_sb_count == 2
    assert metadata.total_mismatched_sbs == 6
    assert metadata.fallback_to_full_pipeline is False


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_mismatch_missing_rows_falls_back_with_metadata(mock_precheck, mock_full):
    """Missing rows on a MISMATCH — full pipeline runs, metadata records the fallback."""
    mock_precheck.return_value = FingerprintResult(
        verdict="MISMATCH",
        source_rows=None,
        target_rows=None,
        solved_count=1,
        unsolved_sb_count=2,
        total_mismatched_sbs=3,
        detection_elapsed_ms=11,
    )
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.verdict == "MISMATCH"
    assert metadata.fallback_to_full_pipeline is True
    # Solver counters from the FingerprintResult must still be preserved on the fallback row.
    assert metadata.solved_count == 1
    assert metadata.unsolved_sb_count == 2
    assert metadata.total_mismatched_sbs == 3
    # The full-pipeline output must be returned, not a synthetic match.
    assert output.mismatch_count == 999


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_precheck_returns_none_records_fallback(mock_precheck, mock_full):
    """Soft skip — orchestrator returned None, no exception. verdict stays None."""
    mock_precheck.return_value = None
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict is None
    assert metadata.fallback_to_full_pipeline is True
    assert output.mismatch_count == 999


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_precheck_failure_records_failed_verdict(mock_precheck, mock_full):
    """Pre-check exceptions are swallowed and attributed via verdict=FAILED."""
    mock_precheck.side_effect = DataSourceRuntimeException("simulated jdbc failure")
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict == "FAILED"
    assert metadata.fallback_to_full_pipeline is True
    assert output.mismatch_count == 999


@pytest.mark.parametrize(
    "report_type",
    ["data", "all", "row"],
)
@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_eligible_match_skips_full_pipeline_for_all_data_report_types(mock_precheck, mock_full, report_type):
    """MATCH short-circuits the full pipeline for ``data`` / ``all`` / ``row`` alike."""
    mock_precheck.return_value = FingerprintResult(verdict="MATCH", detection_elapsed_ms=1)

    TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(report_type=report_type),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    mock_full.assert_not_called()


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_pyspark_exception_during_precheck_falls_back_with_failed_verdict(mock_precheck, mock_full):
    """``compute_target_fingerprint`` materialises a Spark plan at action time;
    ``AnalysisException`` (a ``PySparkException``) raised there must NOT crash
    the recon. The trigger catches it, falls back to the full pipeline, and
    records verdict=FAILED so dashboards can quantify the precheck's reliability.

    Without this catch a column-resolution failure (typical when the user has a
    column-name mismatch and no ``column_mapping``) would propagate up and
    crash ``_do_recon_one``.
    """
    from pyspark.errors import PySparkException

    # PySparkException's __init__ requires a registered error_class; subclassing
    # avoids that constraint and keeps the test focused on the catch widening.
    class _SimulatedAnalysisException(PySparkException):  # noqa: D401
        def __init__(self, msg: str) -> None:  # pylint: disable=super-init-not-called
            self._message = msg

        def __str__(self) -> str:
            return self._message

    mock_precheck.side_effect = _SimulatedAnalysisException(
        "AnalysisException: column 'foo' does not exist on target Delta table"
    )
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict == "FAILED"
    assert metadata.fallback_to_full_pipeline is True
    assert output.mismatch_count == 999, "Full-pipeline output (sentinel mismatch_count=999) must be returned"


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_target_row_count_override_threads_from_reconcile_config(mock_precheck, mock_full):
    """``ReconcileConfig.fingerprint_row_count_override`` is the single
    configuration entry point for tier-pinning; the trigger must thread its
    value into ``run_fingerprint_precheck`` so the orchestrator's
    ``_select_tier`` receives it.
    """
    mock_precheck.return_value = FingerprintResult(verdict="MATCH", detection_elapsed_ms=1)
    mock_full.return_value = _stub_full_pipeline_output()

    config = _config()
    config.fingerprint_row_count_override = 250_000_000

    TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=config,
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    _, call_kwargs = mock_precheck.call_args
    assert call_kwargs["target_row_count_override"] == 250_000_000


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_target_row_count_override_defaults_to_none(mock_precheck, mock_full):
    """The default value carried by ``ReconcileConfig`` propagates as ``None`` so
    legacy configs keep the Delta DESCRIBE DETAIL heuristic behaviour."""
    mock_precheck.return_value = FingerprintResult(verdict="MATCH", detection_elapsed_ms=1)
    mock_full.return_value = _stub_full_pipeline_output()

    TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    _, call_kwargs = mock_precheck.call_args
    assert call_kwargs["target_row_count_override"] is None


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.build_mismatch_output")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_pyspark_exception_during_mismatch_output_falls_back_to_full_pipeline(mock_precheck, mock_build, mock_full):
    """``build_mismatch_output`` runs Spark actions on the prefetched src/tgt
    frames. A Spark failure there (column resolution, NPE in compare layer,
    etc.) must mirror the fail-open pattern used by every other non-MATCH
    branch: fall through to the standard full pipeline so the table still
    gets a real recon answer, and record on the metadata that the
    precheck-built output was rejected. Asserting the FAILED verdict (the
    pre-fail-open behaviour) would hide the regression that prompted the
    review feedback.
    """
    from pyspark.errors import PySparkException

    class _SimulatedAnalysisException(PySparkException):  # noqa: D401
        def __init__(self, msg: str) -> None:  # pylint: disable=super-init-not-called
            self._message = msg

        def __str__(self) -> str:
            return self._message

    mock_precheck.return_value = FingerprintResult(
        verdict="MISMATCH",
        source_rows=MagicMock(),
        target_rows=MagicMock(),
        detection_elapsed_ms=10,
        solved_count=1,
    )
    mock_build.side_effect = _SimulatedAnalysisException("AnalysisException: column resolution failed")
    full_output = _stub_full_pipeline_output()
    mock_full.return_value = full_output

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is True
    assert metadata.verdict == "MISMATCH", (
        "Verdict must reflect the precheck signal, not the build-output failure — "
        "the failure is recorded via fallback_to_full_pipeline=True."
    )
    assert metadata.fallback_to_full_pipeline is True
    assert (
        output is full_output
    ), "Output must come from the full pipeline so the customer still gets a real recon answer."
    mock_full.assert_called_once()


@patch.object(TriggerReconService, "_run_reconcile_data")
@patch("databricks.labs.lakebridge.reconcile.trigger_recon_service.run_fingerprint_precheck")
def test_unmapped_target_column_mapping_records_typed_ineligibility(mock_precheck, mock_full):
    """An ``UnmappedTargetColumnMappingError`` from ``align_columns`` (a config-time
    issue, not a precheck failure) must surface as
    ``ineligibility_reason='unmapped_target_column_mapping'`` on the persisted metric —
    not as a silent ``None`` fallback. Adoption queries against
    ``recon_metrics.fingerprint_metrics.ineligibility_reason`` rely on the typed value
    to quantify column-mapping drift.
    """
    mock_precheck.side_effect = UnmappedTargetColumnMappingError(
        "column_mapping target 'tgt_a_typo' (mapped from 'src_a') not found in target schema"
    )
    mock_full.return_value = _stub_full_pipeline_output()

    output, metadata = TriggerReconService._run_fingerprint_or_reconcile_data(  # pylint: disable=protected-access
        reconciler=_reconciler(),
        reconcile_config=_config(),
        table_conf=_table(),
        src_schema=[],
        tgt_schema=[],
    )

    assert metadata.eligible is False, "Config-time ineligibility, not a precheck failure"
    assert metadata.ineligibility_reason == INELIGIBLE_UNMAPPED_TARGET_COLUMN_MAPPING
    assert metadata.ineligibility_reason == "unmapped_target_column_mapping"
    assert metadata.verdict is None
    assert metadata.fallback_to_full_pipeline is False, (
        "fallback_to_full_pipeline tracks runtime fallbacks; ineligible runs use the full "
        "pipeline by definition and shouldn't double-count on this flag."
    )
    assert output.mismatch_count == 999, "Full-pipeline output (sentinel mismatch_count=999) must be returned"
