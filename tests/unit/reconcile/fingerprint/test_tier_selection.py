"""Unit tests for orchestrator-level adaptive sub-bucket tier selection.

Pins:
  - ``_select_tier`` returns the correct (sub_bucket_count, bucket_count) for a row count.
  - DESCRIBE DETAIL failure falls through to static defaults.
  - Source and target paths receive the same tier (GROUP BY alignment).
  - User override short-circuits the metadata lookup.
  - ``FingerprintResult`` carries tier provenance through to recon_metrics.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pyspark.sql.utils import AnalysisException

from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    BUCKET_COUNT,
    SUB_BUCKET_COUNT,
)
from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import (  # pylint: disable=import-private-name
    _select_tier,
)
from databricks.labs.lakebridge.reconcile.fingerprint.row_count import RowCountSource
from tests.unit.reconcile.fingerprint._fixtures import (
    make_database_config,
    make_describe_detail_df,
    make_table_conf,
)

# --- Tier selection: each row count maps to the expected tier --------------


@pytest.mark.parametrize(
    ("num_records", "expected_sub_buckets", "expected_buckets", "expected_source"),
    [
        # < 50K — sparse-table tier
        (10_000, 16_384, 128, RowCountSource.DELTA_DESCRIBE_DETAIL),
        # 500K – 50M — legacy default tier
        (10_000_000, 1_048_576, 1_024, RowCountSource.DELTA_DESCRIBE_DETAIL),
        # 50M – 500M — NEW tier (P1 100M fixture lands here)
        (100_000_000, 2_097_152, 2_048, RowCountSource.DELTA_DESCRIBE_DETAIL),
        # 500M – 5B — NEW tier (P2 1B fixture lands here)
        (1_000_000_000, 4_194_304, 4_096, RowCountSource.DELTA_DESCRIBE_DETAIL),
        # 5B – 50B — NEW tier (covers 20B+ target)
        (20_000_000_000, 8_388_608, 8_192, RowCountSource.DELTA_DESCRIBE_DETAIL),
        # 50B+ — clamp tier
        (100_000_000_000, 16_777_216, 16_384, RowCountSource.DELTA_DESCRIBE_DETAIL),
    ],
)
def test_select_tier_picks_correct_tier_from_target_delta_count(
    num_records, expected_sub_buckets, expected_buckets, expected_source
):
    """``_select_tier`` reads target ``numRecords`` and returns a tier for both sides."""
    spark = MagicMock()
    spark.sql.return_value = make_describe_detail_df(num_records)
    tier = _select_tier(spark, make_database_config(), make_table_conf())
    assert tier.sub_bucket_count == expected_sub_buckets
    assert tier.bucket_count == expected_buckets
    assert tier.target_row_count == num_records
    assert tier.row_count_source == expected_source.value


def test_select_tier_falls_back_to_static_default_when_describe_detail_fails():
    """DESCRIBE DETAIL failure falls back to legacy static defaults."""
    spark = MagicMock()
    spark.sql.side_effect = AnalysisException("Table or view not found: test_catalog.perf_test.orders")
    tier = _select_tier(spark, make_database_config(), make_table_conf())
    assert tier.sub_bucket_count == SUB_BUCKET_COUNT
    assert tier.bucket_count == BUCKET_COUNT
    assert tier.target_row_count is None
    assert tier.row_count_source == RowCountSource.STATIC_DEFAULT.value


def test_select_tier_uses_target_catalog_and_schema_not_source():
    """Tier comes from the target Delta table; source-side row counts are not consulted."""
    spark = MagicMock()
    spark.sql.return_value = make_describe_detail_df(100_000_000)
    _select_tier(spark, make_database_config(), make_table_conf(target_name="my_target_table"))
    spark.sql.assert_called_once()
    call_arg = spark.sql.call_args[0][0]
    # Must reference TARGET catalog/schema/table, not source.
    assert (
        "test_catalog.perf_test.my_target_table" in call_arg
    ), f"_select_tier must DESCRIBE DETAIL the TARGET table; got SQL: {call_arg!r}"
    assert "source_catalog" not in call_arg, (
        f"_select_tier must NEVER reference source catalog (Redshift side has no Delta metadata); "
        f"got SQL: {call_arg!r}"
    )


def test_select_tier_user_override_short_circuits_describe_detail():
    """``override_row_count`` short-circuits — no spark.sql call at all."""
    spark = MagicMock()
    tier = _select_tier(
        spark,
        make_database_config(),
        make_table_conf(),
        override_row_count=15_800_000_000,
    )
    spark.sql.assert_not_called()
    # 15.8B → 5B-50B tier
    assert tier.sub_bucket_count == 8_388_608
    assert tier.bucket_count == 8_192
    assert tier.target_row_count == 15_800_000_000
    assert tier.row_count_source == RowCountSource.USER_OVERRIDE.value


# --- Source / target receive identical tier --------------------------------


def test_run_fingerprint_precheck_passes_same_tier_to_source_and_target():
    """Source and target must receive the same tier — mismatched moduli mis-align GROUP BY."""
    from databricks.labs.lakebridge.reconcile.fingerprint import (  # pylint: disable=import-outside-toplevel
        orchestrator as orch,
    )
    from databricks.labs.lakebridge.reconcile.fingerprint.engine import (  # pylint: disable=import-outside-toplevel
        DetectionResult,
    )
    from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import (  # pylint: disable=import-outside-toplevel
        ColumnAlignment,
    )
    from databricks.labs.lakebridge.reconcile.recon_config import (  # pylint: disable=import-outside-toplevel
        Schema,
    )

    captured_tier_detection: list = []

    fake_detection = DetectionResult(verdict="MATCH")

    def fake_detection_phase(*args, **kwargs):
        # ``tier`` is the 8th positional argument; tolerate kwarg form too.
        tier = kwargs.get("tier") if "tier" in kwargs else args[7]
        captured_tier_detection.append(tier)
        return fake_detection, 42

    def fake_resolve_cols(*_args, **_kwargs):
        return [Schema("`order_id`", "bigint", "`order_id`", '"order_id"')]

    def fake_align_columns(*_args, **_kwargs):
        return ColumnAlignment(column_mapping=None)

    spark = MagicMock()
    spark.sql.return_value = make_describe_detail_df(100_000_000)
    source = MagicMock()
    target = MagicMock()
    source_engine = MagicMock()

    with (
        patch.object(orch, "_run_detection_phase", side_effect=fake_detection_phase),
        patch.object(orch, "_resolve_detection_columns", side_effect=fake_resolve_cols),
        patch.object(orch, "align_columns", side_effect=fake_align_columns),
        patch.object(orch, "get_query_builder", return_value=MagicMock()),
    ):
        result = orch.run_fingerprint_precheck(
            source=source,
            target=target,
            spark=spark,
            source_engine=source_engine,
            database_config=make_database_config(),
            table_conf=make_table_conf(),
            src_schema=[],
            tgt_schema=[],
            report_type="data",
            data_source="redshift",
        )

    # MATCH verdict — _run_detection_phase was called once with the tier.
    assert len(captured_tier_detection) == 1
    tier = captured_tier_detection[0]
    # 100M → 50M-500M tier
    assert tier.sub_bucket_count == 2_097_152
    assert tier.bucket_count == 2_048

    # Tier provenance flows into ``FingerprintResult``.
    assert result is not None
    assert result.verdict == "MATCH"
    assert result.sub_bucket_count == 2_097_152
    assert result.bucket_count == 2_048
    assert result.target_row_count == 100_000_000
    assert result.row_count_source == "delta_describe_detail"


def test_fingerprint_result_carries_static_default_provenance_on_describe_detail_failure():
    """``FingerprintResult`` carries ``row_count_source="static_default"`` after fall-through."""
    from databricks.labs.lakebridge.reconcile.fingerprint import (  # pylint: disable=import-outside-toplevel
        orchestrator as orch,
    )
    from databricks.labs.lakebridge.reconcile.fingerprint.engine import (  # pylint: disable=import-outside-toplevel
        DetectionResult,
    )
    from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import (  # pylint: disable=import-outside-toplevel
        ColumnAlignment,
    )
    from databricks.labs.lakebridge.reconcile.recon_config import (  # pylint: disable=import-outside-toplevel
        Schema,
    )

    fake_detection = DetectionResult(verdict="MATCH")

    def fake_detection_phase(*_args, **_kwargs):
        return fake_detection, 42

    def fake_resolve_cols(*_args, **_kwargs):
        return [Schema("`order_id`", "bigint", "`order_id`", '"order_id"')]

    def fake_align_columns(*_args, **_kwargs):
        return ColumnAlignment(column_mapping=None)

    spark = MagicMock()
    spark.sql.side_effect = AnalysisException("table not found")
    source = MagicMock()
    target = MagicMock()
    source_engine = MagicMock()

    with (
        patch.object(orch, "_run_detection_phase", side_effect=fake_detection_phase),
        patch.object(orch, "_resolve_detection_columns", side_effect=fake_resolve_cols),
        patch.object(orch, "align_columns", side_effect=fake_align_columns),
        patch.object(orch, "get_query_builder", return_value=MagicMock()),
    ):
        result = orch.run_fingerprint_precheck(
            source=source,
            target=target,
            spark=spark,
            source_engine=source_engine,
            database_config=make_database_config(),
            table_conf=make_table_conf(),
            src_schema=[],
            tgt_schema=[],
            report_type="data",
            data_source="redshift",
        )

    assert result is not None
    assert result.verdict == "MATCH"
    # Static fallback yields the legacy hardcoded tier.
    assert result.sub_bucket_count == SUB_BUCKET_COUNT
    assert result.bucket_count == BUCKET_COUNT
    # And carries the ``static_default`` provenance so it's auditable.
    assert result.target_row_count is None
    assert result.row_count_source == "static_default"
