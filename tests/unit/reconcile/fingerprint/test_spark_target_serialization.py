"""Pin Stage-1 (DataFrame) ↔ Stage-2 (SQL) target serialisation symmetry.

The two helpers in ``spark_target.py`` must produce the same hash inputs
byte-for-byte for any column value. Without that contract, trailing-whitespace
rows surfaced by Stage-1 detection cannot be re-located by Stage-2 surgical
fetch and silently drop from the recon output.
"""

from __future__ import annotations

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.spark_target import (  # pylint: disable=import-private-name
    _quote_spark_identifier,
    _serialize_column_spark_sql,
    build_target_filter_subquery,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema


@pytest.mark.parametrize("treat_empty_as_null", [False, True])
def test_serialize_column_spark_sql_emits_trim(treat_empty_as_null: bool) -> None:
    sql = _serialize_column_spark_sql("notes", "varchar(64)", treat_empty_as_null)
    assert "TRIM(CAST(`notes` AS STRING))" in sql, sql
    assert sql.endswith(", '_null_recon_')")
    if treat_empty_as_null:
        assert "NULLIF(TRIM(" in sql, sql


def test_build_target_filter_subquery_serialises_with_trim() -> None:
    columns = [
        Schema("`order_id`", "int", "`order_id`", "`order_id`"),
        Schema("`notes`", "varchar(64)", "`notes`", "`notes`"),
    ]
    sql = build_target_filter_subquery(
        catalog="test_catalog",
        schema="fp_correctness",
        table="orders",
        columns=columns,
        column_mapping=None,
        solved_hashes={1: [101, 102]},
        unsolved_sb_ids=[7],
        sub_bucket_count=1024,
    )
    assert "TRIM(CAST(`order_id` AS STRING))" in sql, sql
    assert "TRIM(CAST(`notes` AS STRING))" in sql, sql
    # Catalog/schema/table route through ``_quote_spark_identifier``.
    assert "FROM `test_catalog`.`fp_correctness`.`orders`" in sql, sql
    assert "_fp_filtered" in sql


def test_build_target_filter_subquery_omits_catalog_when_unset() -> None:
    sql = build_target_filter_subquery(
        catalog=None,
        schema="schema_only",
        table="orders",
        columns=[Schema("`order_id`", "int", "`order_id`", "`order_id`")],
        column_mapping=None,
        solved_hashes={},
        unsolved_sb_ids=[1, 2],
        sub_bucket_count=1024,
    )
    assert "FROM `schema_only`.`orders`" in sql, sql
    assert "test_catalog." not in sql


def test_quote_spark_identifier_doubles_embedded_backtick() -> None:
    """Defense-in-depth: a column name containing a literal backtick must not break the SQL."""
    assert _quote_spark_identifier("plain") == "`plain`"
    assert _quote_spark_identifier("we`ird") == "`we``ird`"
    # Two embedded backticks → four doubled inside, plus outer pair = six total.
    assert _quote_spark_identifier("``") == "``````"


def test_build_target_filter_subquery_resolves_column_mapping() -> None:
    sql = build_target_filter_subquery(
        catalog="c",
        schema="s",
        table="t",
        columns=[Schema("`src_id`", "int", "`src_id`", "`src_id`")],
        column_mapping={"src_id": "tgt_id"},
        solved_hashes={0: [1]},
        unsolved_sb_ids=[],
        sub_bucket_count=1024,
    )
    assert "`tgt_id`" in sql, sql
    assert "`src_id`" not in sql, sql


@pytest.mark.parametrize("col_type", ["timestamp_ntz", "timestamp without time zone"])
def test_serialize_column_spark_sql_uses_plain_date_format_for_naive_timestamps(col_type: str) -> None:
    """Naive (NTZ) timestamps render directly with ``DATE_FORMAT`` because the
    value carries no timezone semantics — same shape as Redshift's
    ``TO_CHAR(_, 'YYYY-MM-DD HH24:MI:SS.US')`` for ``timestamp without time zone``.
    """
    sql = _serialize_column_spark_sql("ts_col", col_type, treat_empty_as_null=False)
    assert "TRIM(DATE_FORMAT(`ts_col`, 'yyyy-MM-dd HH:mm:ss.SSSSSS'))" in sql, sql
    # NTZ columns must NOT route through the UTC pin — that would shift values
    # that have no associated timezone.
    assert "TO_UTC_TIMESTAMP" not in sql, sql
    assert "CAST(`ts_col` AS STRING)" not in sql, sql


@pytest.mark.parametrize(
    "col_type",
    ["timestamp", "timestamp_ltz", "timestamp with time zone", "timestamp with local time zone"],
)
def test_serialize_column_spark_sql_pins_utc_for_tz_aware_timestamps(col_type: str) -> None:
    """TZ-aware (LTZ) Spark timestamps must be normalised to the UTC wall-clock
    before formatting so the bytes match Redshift's
    ``TO_CHAR(_ AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')`` regardless of
    the cluster's ``spark.sql.session.timeZone``. Without this pin, a non-UTC
    session timezone would render the same instant differently on the two
    sides and Stage-1 would over-report mismatches on every TZ-aware column.
    """
    sql = _serialize_column_spark_sql("ts_col", col_type, treat_empty_as_null=False)
    assert (
        "TRIM(DATE_FORMAT(TO_UTC_TIMESTAMP(`ts_col`, CURRENT_TIMEZONE()), " "'yyyy-MM-dd HH:mm:ss.SSSSSS'))"
    ) in sql, sql
    assert "CAST(`ts_col` AS STRING)" not in sql, sql


def test_serialize_column_spark_sql_uses_date_format_for_date() -> None:
    """``date`` columns format to ``yyyy-MM-dd`` to match Redshift's ``TO_CHAR(_, 'YYYY-MM-DD')``."""
    sql = _serialize_column_spark_sql("d_col", "date", treat_empty_as_null=False)
    assert "DATE_FORMAT(`d_col`, 'yyyy-MM-dd')" in sql, sql


def test_build_target_filter_subquery_uses_date_format_for_timestamps() -> None:
    """Stage-2 SQL filter subquery must inherit the timestamp format end-to-end.

    Pins both timestamp families: the TZ-aware default ``timestamp`` routes
    through the UTC pin; the NTZ variant renders directly. Both share the
    same fractional-second precision so the bytes match Redshift.
    """
    columns = [
        Schema("`order_id`", "int", "`order_id`", "`order_id`"),
        Schema("`event_ts`", "timestamp", "`event_ts`", "`event_ts`"),
        Schema("`event_ts_naive`", "timestamp_ntz", "`event_ts_naive`", "`event_ts_naive`"),
        Schema("`event_dt`", "date", "`event_dt`", "`event_dt`"),
    ]
    sql = build_target_filter_subquery(
        catalog="c",
        schema="s",
        table="t",
        columns=columns,
        column_mapping=None,
        solved_hashes={1: [101]},
        unsolved_sb_ids=[],
        sub_bucket_count=1024,
    )
    # TZ-aware: UTC-pinned via TO_UTC_TIMESTAMP.
    assert ("TO_UTC_TIMESTAMP(`event_ts`, CURRENT_TIMEZONE()), " "'yyyy-MM-dd HH:mm:ss.SSSSSS'") in sql, sql
    # NTZ: direct DATE_FORMAT, no pin.
    assert "DATE_FORMAT(`event_ts_naive`, 'yyyy-MM-dd HH:mm:ss.SSSSSS')" in sql, sql
    assert "DATE_FORMAT(`event_dt`, 'yyyy-MM-dd')" in sql, sql
    # The non-temporal column still uses the default cast path.
    assert "CAST(`order_id` AS STRING)" in sql, sql


def test_serialize_column_spark_sql_handles_dot_in_column_name() -> None:
    """Delta column names containing ``.`` must be backtick-escaped on the SQL
    path so Spark resolves the column literally instead of as a struct field
    path. Without escaping, ``F.col("a.b")`` would attempt ``a.b`` resolution
    and crash mid-Stage-1; ``_quote_spark_identifier`` is applied on both paths.
    """
    sql = _serialize_column_spark_sql("a.b", "string", treat_empty_as_null=False)
    assert "`a.b`" in sql, sql
    assert "CAST(`a.b` AS STRING)" in sql, sql


def test_quote_spark_identifier_handles_dot() -> None:
    """``.`` inside an identifier must be wrapped without further treatment — the
    backtick fence is what tells Spark "this is a column name, not a struct path".
    """
    assert _quote_spark_identifier("a.b") == "`a.b`"
    assert _quote_spark_identifier("event.timestamp.utc") == "`event.timestamp.utc`"
