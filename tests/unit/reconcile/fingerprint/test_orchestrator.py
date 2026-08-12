"""Unit tests for fingerprint orchestrator helpers."""

from unittest.mock import create_autospec

import pytest
from pyspark.sql.types import DecimalType

from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.fingerprint import orchestrator, spark_target
from databricks.labs.lakebridge.reconcile.fingerprint.engine import DetectionResult, SolveResult
from databricks.labs.lakebridge.reconcile.fingerprint.exceptions import UnmappedTargetColumnMappingError
from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import (
    resolve_detection_columns,
    align_columns,
    collect_solved_hashes,
    fingerprint_supported_sources,
    get_query_builder,
)
from databricks.labs.lakebridge.reconcile.recon_config import ColumnMapping
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.redshift import (
    RedshiftFingerprintQueryBuilder,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema, Table
from databricks.labs.lakebridge.reconcile.recon_output_config import DataReconcileOutput, MismatchOutput


def test_collect_solved_hashes_merges_same_sub_bucket():
    """Same sub_bucket_id can appear across multiple bucket_id aggregates; hashes must merge."""
    detection = DetectionResult(
        verdict="MISMATCH",
        solved_results=[
            SolveResult(sub_bucket_id=7, source_hashes=[100], target_hashes=[]),
            SolveResult(sub_bucket_id=7, source_hashes=[], target_hashes=[200]),
        ],
    )
    out = collect_solved_hashes(detection)
    assert out[7] == [100, 200]


def test_collect_solved_hashes_dedupes_within_and_across_solves():
    """The same hash can appear on both source and target sides AND across multiple
    SolveResult rows that share a sub_bucket_id. Dedupe driver-side so the held dict
    stays O(distinct hashes); the downstream WHERE-clause set comprehension was already
    deduping but only after we paid the memory.
    """
    detection = DetectionResult(
        verdict="MISMATCH",
        solved_results=[
            SolveResult(sub_bucket_id=7, source_hashes=[100, 200], target_hashes=[100]),
            SolveResult(sub_bucket_id=7, source_hashes=[100, 300], target_hashes=[]),
        ],
    )
    out = collect_solved_hashes(detection)
    assert out[7] == [100, 200, 300]


def test_align_columns_rejects_unmapped_target_column():
    """A typo in ``column_mapping.target_name`` raises a typed exception so the
    trigger layer can record ``UNMAPPED_TARGET_COLUMN_MAPPING`` on the persisted
    metric (instead of a silent ``None`` fallback that adoption queries can't see).
    """
    table_conf = Table(
        source_name="orders",
        target_name="orders",
        join_columns=["order_id"],
        column_mapping=[ColumnMapping(source_name="src_a", target_name="tgt_a_typo")],
    )
    src_schema = [
        Schema("`src_a`", "int", '"src_a"'),
        Schema("`order_id`", "bigint", '"order_id"'),
    ]
    tgt_schema = [
        Schema("`tgt_a`", "int", "`tgt_a`"),
        Schema("`order_id`", "bigint", "`order_id`"),
    ]
    with pytest.raises(UnmappedTargetColumnMappingError, match="tgt_a_typo"):
        align_columns(table_conf, src_schema, tgt_schema)


def test_align_columns_accepts_validated_target_column_mapping():
    """The happy path: a real target column name passes."""
    table_conf = Table(
        source_name="orders",
        target_name="orders",
        join_columns=["order_id"],
        column_mapping=[ColumnMapping(source_name="src_a", target_name="tgt_a")],
    )
    src_schema = [
        Schema("`src_a`", "int", '"src_a"'),
        Schema("`order_id`", "bigint", '"order_id"'),
    ]
    tgt_schema = [
        Schema("`tgt_a`", "int", "`tgt_a`"),
        Schema("`order_id`", "bigint", "`order_id`"),
    ]
    alignment = align_columns(table_conf, src_schema, tgt_schema)
    assert alignment is not None
    assert alignment.column_mapping == {"src_a": "tgt_a"}


def test_query_builder_registry_returns_redshift_builder():
    builder = get_query_builder("redshift")
    assert isinstance(builder, RedshiftFingerprintQueryBuilder)


def test_query_builder_does_not_collapse_empty_to_null():
    """Fingerprint serialization keeps '' distinct from NULL, matching the row-hash
    convention in expression_generator (TRIM does not collapse '' to NULL). The shared
    transform map has no empty-as-null knob, so no NULLIF can leak in.
    """
    builder = get_query_builder("redshift")
    serialized = builder.serialize_column("`notes`", "VARCHAR")
    assert "NULLIF" not in serialized
    assert serialized == 'COALESCE(TRIM("notes"), \'_null_recon_\')'


def test_query_builder_registry_rejects_unknown_source():
    with pytest.raises(ValueError, match="No fingerprint query builder registered"):
        get_query_builder("mysql")


def test_fingerprint_supported_sources_contains_redshift():
    """fingerprint_supported_sources is the source of truth for the eligibility guard."""
    supported = fingerprint_supported_sources()
    assert "redshift" in supported
    for source in supported:
        assert get_query_builder(source) is not None


def test_resolve_detection_columns_strips_identifier_delimiters():
    """Schema entries are ANSI-delimited via _map_meta_column; user-supplied join_columns
    are bare. The resolver must reconcile both forms (and dedupe across raw / delimited
    overlap) so fingerprint isn't silently disabled on every real connector.
    """
    src_schema = [
        Schema("`color`", "varchar(2)", '"color"'),
        Schema("`clarity`", "varchar(5)", '"clarity"'),
        Schema("`carat`", "decimal(5,2)", '"carat"'),
    ]
    table_conf = Table(
        source_name="diamonds",
        target_name="diamonds",
        join_columns=["color", "clarity"],
        select_columns=["color", "clarity"],
    )
    source = create_autospec(DataSource, instance=True)
    source.normalize_identifier.side_effect = lambda ident: type(
        "NI", (), {"ansi_normalized": f"`{ident}`", "source_normalized": f'"{ident}"'}
    )()

    resolved = resolve_detection_columns(table_conf, src_schema, source, get_dialect("redshift"))

    assert resolved is not None
    resolved_names = [_strip_delim(s.ansi_normalized_column_name) for s in resolved]
    assert sorted(resolved_names) == ["clarity", "color"]


def _strip_delim(name: str) -> str:
    return DialectUtils.unnormalize_identifier(name)


def test_build_mismatch_output_backfills_mismatch_columns_for_report_all(monkeypatch):
    """Unit-level wiring check for the ``mismatch_columns`` backfill.

    The fingerprint MISMATCH path calls ``build_mismatch_output`` ->
    ``compare.reconcile_data``. ``reconcile_data`` populates ``mismatch_df`` but
    leaves ``mismatch_columns`` at its default. Because the fingerprint Stage-2
    frames already carry every hashed column (``project_all_columns=True``), the
    orchestrator backfills the column-level detail by routing the prefetched
    frames through the SAME ``capture_mismatch_data_and_columns`` the normal
    sampled path uses (rather than a fingerprint-specific diff). The null-safe
    per-column match and the genuine-mismatch filter live inside that shared
    compare-layer function, so the orchestrator simply passes its output through.

    Without this, every ``report_type='all'`` cell would land with
    ``mismatch_columns=[]`` even though the row counts were right.
    """
    captured_calls: dict = {}

    fake_skinny_mismatch_df = object()
    # capture.mismatch_df carries _base/_compare/_match for every check column
    # (its shape is exercised end-to-end in the integration compare tests).
    fake_wide_capture_df = object()
    # filter_to_row_mismatches drops the column-matching rows the Stage-2 sub-bucket fetch
    # can include; the orchestrator must route capture's frame through it before returning.
    fake_filtered_mismatch_df = object()

    def fake_filter_to_row_mismatches(mismatch_df):
        captured_calls["filter_to_row_mismatches"] = mismatch_df
        return fake_filtered_mismatch_df

    def fake_compare_reconcile_data(*, source, target, key_columns, report_type, persistence):
        del persistence  # accepted to match the real signature, not needed here
        captured_calls["compare_reconcile_data"] = {
            "source": source,
            "target": target,
            "key_columns": key_columns,
            "report_type": report_type,
        }
        return DataReconcileOutput(
            mismatch_count=3,
            missing_in_src_count=0,
            missing_in_tgt_count=0,
            mismatch=MismatchOutput(mismatch_df=fake_skinny_mismatch_df, mismatch_columns=None),
        )

    def fake_capture_mismatch_data_and_columns(*, source, target, key_columns, persistence):
        del persistence  # accepted to match the real signature, not needed here
        captured_calls["capture_mismatch_data_and_columns"] = {
            "source_columns": list(source.columns),  # must NOT contain hash_value_recon
            "target_columns": list(target.columns),
            "key_columns": key_columns,
        }
        return MismatchOutput(mismatch_df=fake_wide_capture_df, mismatch_columns=["s_name", "s_acctbal"])

    monkeypatch.setattr(orchestrator, "compare_reconcile_data", fake_compare_reconcile_data)
    monkeypatch.setattr(orchestrator, "capture_mismatch_data_and_columns", fake_capture_mismatch_data_and_columns)
    monkeypatch.setattr(orchestrator, "filter_to_row_mismatches", fake_filter_to_row_mismatches)

    # Build minimal stand-ins: only need .columns and .drop().
    class FakeDF:
        def __init__(self, cols):
            self.columns = list(cols)

        def drop(self, name):
            return FakeDF([c for c in self.columns if c != name])

    src = FakeDF(["s_suppkey", "s_nationkey", "s_name", "s_acctbal", "hash_value_recon"])
    tgt = FakeDF(["s_suppkey", "s_nationkey", "s_name", "s_acctbal", "hash_value_recon"])

    out = orchestrator.build_mismatch_output(
        src_hashed=src,
        tgt_hashed=tgt,
        key_columns=["s_suppkey", "s_nationkey"],
        report_type="all",
        persistence=None,
    )

    # mismatch_columns must be the list capture_mismatch returned, not the empty default.
    assert out.mismatch.mismatch_columns == ["s_name", "s_acctbal"]
    # capture's WIDE frame (with _base/_compare/_match triples) must be routed through
    # filter_to_row_mismatches, and the FILTERED frame returned — not the skinny frame from
    # compare.reconcile_data, and not capture's unfiltered frame.
    assert captured_calls["filter_to_row_mismatches"] is fake_wide_capture_df
    assert out.mismatch.mismatch_df is fake_filtered_mismatch_df
    assert out.mismatch.mismatch_df is not fake_skinny_mismatch_df
    assert out.mismatch_count == 3
    # capture_mismatch_data_and_columns must NOT see hash_value_recon (the synthetic
    # hash column would otherwise always show as a mismatched column).
    assert "hash_value_recon" not in captured_calls["capture_mismatch_data_and_columns"]["source_columns"]
    assert "hash_value_recon" not in captured_calls["capture_mismatch_data_and_columns"]["target_columns"]


def test_build_mismatch_output_skips_capture_for_report_data(monkeypatch):
    """For ``report_type='data'`` we don't need column-level diff; the orchestrator
    must skip ``capture_mismatch_data_and_columns`` entirely (it's an O(driver-collect)
    operation on the mismatch_df).
    """
    capture_call_count = {"n": 0}

    def fake_compare_reconcile_data(*_args, **_kwargs):
        return DataReconcileOutput(
            mismatch_count=5,
            mismatch=MismatchOutput(mismatch_df=object(), mismatch_columns=None),
        )

    def fake_capture(*_args, **_kwargs):
        capture_call_count["n"] += 1
        return MismatchOutput(mismatch_df=object(), mismatch_columns=["should_not_appear"])

    monkeypatch.setattr(orchestrator, "compare_reconcile_data", fake_compare_reconcile_data)
    monkeypatch.setattr(orchestrator, "capture_mismatch_data_and_columns", fake_capture)

    out = orchestrator.build_mismatch_output(
        src_hashed=None,
        tgt_hashed=None,
        key_columns=["k"],
        report_type="data",
        persistence=None,
    )

    assert capture_call_count["n"] == 0
    assert out.mismatch.mismatch_columns is None


def test_build_mismatch_output_skips_capture_when_no_mismatches(monkeypatch):
    """If mismatch_count == 0, skip the capture call regardless of report_type."""
    capture_call_count = {"n": 0}

    def fake_compare_reconcile_data(*_args, **_kwargs):
        return DataReconcileOutput(mismatch_count=0, mismatch=MismatchOutput())

    def fake_capture(*_args, **_kwargs):
        capture_call_count["n"] += 1
        return MismatchOutput()

    monkeypatch.setattr(orchestrator, "compare_reconcile_data", fake_compare_reconcile_data)
    monkeypatch.setattr(orchestrator, "capture_mismatch_data_and_columns", fake_capture)

    out = orchestrator.build_mismatch_output(
        src_hashed=None,
        tgt_hashed=None,
        key_columns=["k"],
        report_type="all",
        persistence=None,
    )

    assert capture_call_count["n"] == 0
    assert out.mismatch_count == 0


def test_spark_target_uses_decimal_precision_for_hash_aggregates():
    """Spark target must mirror the Redshift DECIMAL(19,0)/DECIMAL(38,0) precision.

    LongType silently wraps on rh*rh for rh > 2^31, so two rows with large hashes
    can produce equal-but-wrong p2 sums on Spark while Redshift raises a hard
    overflow — making the engine join report false MATCH.
    """
    assert spark_target.RH_OPERAND_TYPE == DecimalType(19, 0)
    assert spark_target.AGG_TYPE == DecimalType(38, 0)
