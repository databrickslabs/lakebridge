"""Unit tests for the ``fingerprint_metrics`` SQL projection in ``ReconCapture``.

String-level tests against the named_struct fragment — no SparkSession or Delta
write loop required. The persisted column shape is the dashboard contract; locking
it here means a stray f-string edit can't silently break it.
"""

import re

from databricks.labs.lakebridge.reconcile.fingerprint.metadata import (
    INELIGIBLE_FILTERS_CONFIGURED,
    FingerprintRunMetadata,
)
from databricks.labs.lakebridge.reconcile.recon_capture import ReconCapture


_FIELD_ORDER = (
    "eligible",
    "ineligibility_reason",
    "verdict",
    "elapsed_ms",
    "solved_count",
    "unsolved_sb_count",
    "total_mismatched_sbs",
    "fallback_to_full_pipeline",
    "sub_bucket_count",
    "bucket_count",
    "target_row_count",
    "row_count_source",
    "fetch_path",
)


def _field_offsets(sql: str) -> list[int]:
    return [sql.index(f"'{name}'") for name in _FIELD_ORDER]


def test_struct_sql_emits_all_eight_fields_in_declared_order():
    """Field order must match the dataclass declaration so mergeSchema widens the
    column to the expected struct shape on first write (Spark resolves struct fields
    positionally during saveAsTable).
    """
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MATCH",
        elapsed_ms=42,
        solved_count=3,
        unsolved_sb_count=1,
        total_mismatched_sbs=4,
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    offsets = _field_offsets(sql)
    assert offsets == sorted(offsets), f"Field order drifted in {sql!r}"


def test_struct_sql_renders_eligible_match_verdict():
    metadata = FingerprintRunMetadata(
        eligible=True, verdict="MATCH", elapsed_ms=120, solved_count=0, unsolved_sb_count=0
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'eligible', true" in sql
    assert "'verdict', 'MATCH'" in sql
    assert "'elapsed_ms', cast(120 as bigint)" in sql
    assert "'fallback_to_full_pipeline', false" in sql


def test_struct_sql_renders_ineligible_with_reason():
    metadata = FingerprintRunMetadata.ineligible(INELIGIBLE_FILTERS_CONFIGURED)
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'eligible', false" in sql
    assert f"'ineligibility_reason', '{INELIGIBLE_FILTERS_CONFIGURED}'" in sql
    assert "'verdict', NULL" in sql


def test_struct_sql_emits_null_for_none_string_fields():
    """None must serialise to SQL NULL, not the literal string 'None' — otherwise
    dashboards filtering on IS NULL miss every row.
    """
    metadata = FingerprintRunMetadata(eligible=True, verdict=None)
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'verdict', NULL" in sql
    assert "'verdict', 'None'" not in sql
    assert "'ineligibility_reason', NULL" in sql


def test_struct_sql_scrubs_quotes_from_reason_and_verdict():
    """Embedded quotes would break the SQL. Scrubbing mirrors exception_message handling."""
    metadata = FingerprintRunMetadata(eligible=False, ineligibility_reason="bad'reason\"here", verdict="MIS\"MATCH")
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "bad'reason" not in sql
    assert 'MIS"MATCH' not in sql
    assert "'ineligibility_reason', 'badreasonhere'" in sql
    assert "'verdict', 'MISMATCH'" in sql


def test_struct_sql_casts_counters_to_bigint():
    """Every counter must carry an explicit bigint cast.

    Without one, Spark infers int for small literals and later rows with larger counts
    force a slow column-type rewrite. Counters: elapsed_ms, solved_count,
    unsolved_sb_count, total_mismatched_sbs, sub_bucket_count, bucket_count,
    target_row_count.
    """
    metadata = FingerprintRunMetadata(
        eligible=True,
        elapsed_ms=1,
        solved_count=2,
        unsolved_sb_count=3,
        total_mismatched_sbs=4,
        sub_bucket_count=2_097_152,
        bucket_count=2_048,
        target_row_count=100_000_000,
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    bigint_casts = re.findall(r"cast\(\d+ as bigint\)", sql)
    assert len(bigint_casts) == 7, f"Expected 7 bigint casts, got {bigint_casts!r}"


def test_struct_sql_target_row_count_null_when_static_default_path():
    """target_row_count emits NULL (not cast(0 as bigint)) when the row-count fetch
    fell through, so dashboards can distinguish "unavailable" from "actually 0".
    """
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MATCH",
        sub_bucket_count=1_048_576,
        bucket_count=32_768,
        target_row_count=None,
        row_count_source="static_default",
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'target_row_count', NULL" in sql
    assert "'row_count_source', 'static_default'" in sql


def test_struct_sql_emits_tier_fields_for_user_override_path():
    """User override surfaces both the tier values and the user_override provenance."""
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MATCH",
        sub_bucket_count=4_194_304,
        bucket_count=4_096,
        target_row_count=1_000_000_000,
        row_count_source="user_override",
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'sub_bucket_count', cast(4194304 as bigint)" in sql
    assert "'bucket_count', cast(4096 as bigint)" in sql
    assert "'target_row_count', cast(1000000000 as bigint)" in sql
    assert "'row_count_source', 'user_override'" in sql


def test_struct_sql_default_metadata_emits_zero_tier_and_null_row_count():
    """Default metadata (ineligible / disabled rows) emits zero tier values and NULL
    for row_count, row_count_source, fetch_path.
    """
    metadata = FingerprintRunMetadata.disabled()
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'sub_bucket_count', cast(0 as bigint)" in sql
    assert "'bucket_count', cast(0 as bigint)" in sql
    assert "'target_row_count', NULL" in sql
    assert "'row_count_source', NULL" in sql
    assert "'fetch_path', NULL" in sql


def test_struct_sql_emits_fetch_path_v2_for_redshift_split():
    """The v2_redshift_split value is preserved so historical recon_metrics rows
    continue to round-trip; current code never emits it.
    """
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MISMATCH",
        sub_bucket_count=2_097_152,
        bucket_count=2_048,
        target_row_count=100_000_000,
        row_count_source="delta_describe_detail",
        fetch_path="v2_redshift_split",
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'fetch_path', 'v2_redshift_split'" in sql


def test_struct_sql_emits_fetch_path_v1_for_legacy_sandwich():
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MISMATCH",
        sub_bucket_count=2_097_152,
        bucket_count=2_048,
        target_row_count=100_000_000,
        row_count_source="delta_describe_detail",
        fetch_path="v1_sandwich",
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'fetch_path', 'v1_sandwich'" in sql


def test_struct_sql_match_verdict_emits_null_fetch_path():
    """MATCH never executes Stage-2; fetch_path must stay NULL even when other tier
    fields are populated.
    """
    metadata = FingerprintRunMetadata(
        eligible=True,
        verdict="MATCH",
        sub_bucket_count=2_097_152,
        bucket_count=2_048,
        target_row_count=100_000_000,
        row_count_source="delta_describe_detail",
        fetch_path=None,
    )
    sql = ReconCapture._fingerprint_metrics_struct_sql(metadata)  # pylint: disable=protected-access
    assert "'fetch_path', NULL" in sql
    assert "'fetch_path', 'None'" not in sql
