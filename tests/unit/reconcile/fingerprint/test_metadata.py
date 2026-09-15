"""Unit tests for the fingerprint metadata dataclass and ineligibility classifier.

Locks the persisted Delta schema contract for ``recon_metrics.fingerprint_metrics`` —
field names, default values, factory shapes, ineligibility-reason enum values. Renaming
any of these is a breaking change for downstream dashboards.
"""

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.metadata import (
    INELIGIBLE_COLUMN_THRESHOLDS_CONFIGURED,
    INELIGIBLE_FILTERS_CONFIGURED,
    INELIGIBLE_FLAG_DISABLED,
    INELIGIBLE_NO_JOIN_COLUMNS,
    INELIGIBLE_REPORT_TYPE_NOT_DATA,
    INELIGIBLE_TABLE_THRESHOLDS_CONFIGURED,
    INELIGIBLE_TRANSFORMS_CONFIGURED,
    INELIGIBLE_UNSUPPORTED_DIALECT,
    FingerprintRunMetadata,
)
from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import classify_ineligibility
from databricks.labs.lakebridge.reconcile.recon_config import (
    ColumnThresholds,
    Filters,
    Table,
    TableThresholds,
    Transformation,
)


def test_metadata_default_state_is_ineligible_zeros():
    """Defaults are safe to write — no eligibility, no verdict, all counters zero."""
    metadata = FingerprintRunMetadata()
    assert metadata.eligible is False
    assert metadata.ineligibility_reason is None
    assert metadata.verdict is None
    assert metadata.elapsed_ms == 0
    assert metadata.solved_count == 0
    assert metadata.unsolved_sb_count == 0
    assert metadata.total_mismatched_sbs == 0
    assert metadata.fallback_to_full_pipeline is False


def test_disabled_factory_records_flag_disabled_reason():
    """``disabled()`` carries ``ineligibility_reason="flag_disabled"`` so adoption queries
    on non-fingerprint reconciles get a non-NULL reason to filter on.
    """
    metadata = FingerprintRunMetadata.disabled()
    assert metadata.eligible is False
    assert metadata.ineligibility_reason == INELIGIBLE_FLAG_DISABLED


def test_ineligible_factory_records_supplied_reason():
    metadata = FingerprintRunMetadata.ineligible(INELIGIBLE_FILTERS_CONFIGURED)
    assert metadata.eligible is False
    assert metadata.ineligibility_reason == INELIGIBLE_FILTERS_CONFIGURED


def _eligible_table() -> Table:
    """Smallest valid Table for a fingerprint-eligible reconcile."""
    return Table(source_name="t", target_name="t", join_columns=["id"])


def test_classify_ineligibility_eligible_path_returns_none():
    assert (
        classify_ineligibility(
            flag_enabled=True,
            data_source="redshift",
            report_type="data",
            table_conf=_eligible_table(),
        )
        is None
    )


def test_classify_flag_disabled_takes_precedence_over_per_table_config():
    """Flag-off reason wins over per-table ineligibility; otherwise the actual feature
    state is hidden in dashboards behind a misleading per-table reason.
    """
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        filters=Filters(source="x is not null"),
    )
    assert (
        classify_ineligibility(flag_enabled=False, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_FLAG_DISABLED
    )


def test_classify_unsupported_dialect_takes_precedence_over_per_table_config():
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        transformations=[Transformation(column_name="x", source="upper(x)")],
    )
    assert (
        classify_ineligibility(
            flag_enabled=True,
            data_source="snowflake",
            report_type="data",
            table_conf=table,
        )
        == INELIGIBLE_UNSUPPORTED_DIALECT
    )


@pytest.mark.parametrize("report_type", ["schema"])
def test_classify_report_type_not_data(report_type: str):
    assert (
        classify_ineligibility(
            flag_enabled=True,
            data_source="redshift",
            report_type=report_type,
            table_conf=_eligible_table(),
        )
        == INELIGIBLE_REPORT_TYPE_NOT_DATA
    )


def test_classify_no_join_columns_blocks_data_path():
    table = Table(source_name="t", target_name="t")
    assert (
        classify_ineligibility(flag_enabled=True, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_NO_JOIN_COLUMNS
    )


def test_classify_filters_configured():
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        filters=Filters(source="created_at > '2024-01-01'"),
    )
    assert (
        classify_ineligibility(flag_enabled=True, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_FILTERS_CONFIGURED
    )


def test_classify_transforms_configured():
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        transformations=[Transformation(column_name="x", source="upper(x)")],
    )
    assert (
        classify_ineligibility(flag_enabled=True, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_TRANSFORMS_CONFIGURED
    )


def test_classify_column_thresholds_configured():
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        column_thresholds=[ColumnThresholds(column_name="amount", lower_bound="-1", upper_bound="1", type="number")],
    )
    assert (
        classify_ineligibility(flag_enabled=True, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_COLUMN_THRESHOLDS_CONFIGURED
    )


def test_classify_table_thresholds_configured():
    table = Table(
        source_name="t",
        target_name="t",
        join_columns=["id"],
        table_thresholds=[TableThresholds(lower_bound="0", upper_bound="10", model="mismatch")],
    )
    assert (
        classify_ineligibility(flag_enabled=True, data_source="redshift", report_type="data", table_conf=table)
        == INELIGIBLE_TABLE_THRESHOLDS_CONFIGURED
    )
