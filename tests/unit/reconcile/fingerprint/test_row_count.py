"""Unit tests for the fingerprint target row-count fetcher.

Three-step fallback chain:
  1. ``override_row_count`` -> USER_OVERRIDE
  2. ``DESCRIBE DETAIL`` succeeds -> DELTA_DESCRIBE_DETAIL
  3. Any failure -> STATIC_DEFAULT with row_count=None

The fetcher must never raise — tier selection is best-effort.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pyspark.sql.utils import AnalysisException

from databricks.labs.lakebridge.reconcile.fingerprint.row_count import (
    RowCountResult,
    RowCountSource,
    fetch_target_row_count,
)


def _make_describe_detail_df(*, columns: list[str], rows: list[dict]) -> MagicMock:
    """Mock DataFrame mimicking ``DESCRIBE DETAIL`` (columns + select.collect)."""
    df = MagicMock()
    df.columns = columns

    if rows is None or "numRecords" not in columns:
        return df

    select_result = MagicMock()
    select_result.collect.return_value = [_RowLike(r) for r in rows]
    df.select.return_value = select_result
    return df


class _RowLike:
    """Mimics PySpark Row ``row['key']`` access."""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def __getitem__(self, key):
        return self._mapping[key]


def _make_spark(describe_detail_df: MagicMock | Exception) -> MagicMock:
    """Mock SparkSession whose ``.sql()`` returns the DataFrame or raises the exception."""
    spark = MagicMock()
    if isinstance(describe_detail_df, Exception):
        spark.sql.side_effect = describe_detail_df
    else:
        spark.sql.return_value = describe_detail_df
    return spark


# --- Path 1: user override ----------------------------------------------------


def test_user_override_short_circuits_chain():
    """Positive override skips DESCRIBE DETAIL entirely."""
    spark = MagicMock()
    result = fetch_target_row_count(
        spark,
        catalog="test_catalog",
        schema="perf_test",
        table="orders",
        override_row_count=100_000_000,
    )
    assert result == RowCountResult(row_count=100_000_000, source=RowCountSource.USER_OVERRIDE)
    spark.sql.assert_not_called()


@pytest.mark.parametrize("override", [0, -1, None])
def test_user_override_zero_or_none_falls_through_to_describe_detail(override):
    """Non-positive overrides are treated as "not given"; DESCRIBE DETAIL still runs."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": 42}])
    spark = _make_spark(df)
    result = fetch_target_row_count(
        spark,
        catalog="test_catalog",
        schema="perf_test",
        table="orders",
        override_row_count=override,
    )
    assert result.source == RowCountSource.DELTA_DESCRIBE_DETAIL
    assert result.row_count == 42


# --- Path 2: DESCRIBE DETAIL success ------------------------------------------


def test_describe_detail_returns_num_records_for_delta_table():
    df = _make_describe_detail_df(
        columns=["format", "id", "name", "numFiles", "numRecords", "createdAt"],
        rows=[{"numRecords": 100_000_000}],
    )
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=100_000_000, source=RowCountSource.DELTA_DESCRIBE_DETAIL)
    spark.sql.assert_called_once_with("DESCRIBE DETAIL test_catalog.perf_test.orders")


def test_describe_detail_works_without_catalog():
    """Two-part ``schema.table`` naming for hive_metastore-style references."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": 1_000}])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog=None, schema="default", table="orders")
    assert result.row_count == 1_000
    spark.sql.assert_called_once_with("DESCRIBE DETAIL default.orders")


def test_describe_detail_zero_rows_is_legitimate():
    """Empty-table case: numRecords=0 is a valid result, not a fall-through."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": 0}])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=0, source=RowCountSource.DELTA_DESCRIBE_DETAIL)


# --- Path 3: fallback to STATIC_DEFAULT ---------------------------------------


def test_table_not_found_falls_back_to_static_default():
    """``AnalysisException`` must not propagate; tier selection is best-effort."""
    spark = _make_spark(AnalysisException("Table or view not found: test_catalog.perf_test.bogus"))
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="bogus")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_unexpected_exception_falls_back_to_static_default():
    """Unexpected errors must not propagate (tier selection is best-effort)."""
    spark = _make_spark(RuntimeError("kerberos creds expired"))
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_non_delta_target_with_no_num_records_column_falls_back():
    """Non-Delta target — DESCRIBE DETAIL succeeds but the column is absent."""
    df = _make_describe_detail_df(
        columns=["format", "id", "name"],  # no numRecords
        rows=[],
    )
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_describe_detail_returning_zero_rows_falls_back():
    """Defensive: zero rows from DESCRIBE DETAIL must not IndexError."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_describe_detail_returning_null_num_records_falls_back():
    """numRecords NULL (per-file stats disabled) must fall through, not feed None to the tier selector."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": None}])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_describe_detail_returning_unexpected_type_falls_back():
    """Defensive against driver/SDK drift: non-int numRecords falls through."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": "100000000"}])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


def test_describe_detail_returning_negative_num_records_falls_back():
    """Negative numRecords is a corruption signal; fall through."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": -1}])
    spark = _make_spark(df)
    result = fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert result == RowCountResult(row_count=None, source=RowCountSource.STATIC_DEFAULT)


# --- Audit trail / logging ----------------------------------------------------


def test_static_default_path_emits_warning_log(caplog):
    """Static-default fallback must log at WARNING for operator visibility."""
    spark = _make_spark(AnalysisException("not found"))
    with caplog.at_level("WARNING"):
        fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert any(
        "row_count_source=static_default" in rec.message for rec in caplog.records
    ), "STATIC_DEFAULT fallback must log at WARNING level for operator visibility"


def test_delta_describe_detail_path_emits_info_log(caplog):
    """Success path logs INFO with the ``key=value`` structured shape."""
    df = _make_describe_detail_df(columns=["numRecords"], rows=[{"numRecords": 100_000_000}])
    spark = _make_spark(df)
    with caplog.at_level("INFO"):
        fetch_target_row_count(spark, catalog="test_catalog", schema="perf_test", table="orders")
    assert any(
        "row_count_source=delta_describe_detail" in rec.message and "row_count=100000000" in rec.message
        for rec in caplog.records
    )


def test_user_override_path_emits_info_log(caplog):
    spark = MagicMock()
    with caplog.at_level("INFO"):
        fetch_target_row_count(
            spark,
            catalog="test_catalog",
            schema="perf_test",
            table="orders",
            override_row_count=100_000_000,
        )
    assert any(
        "row_count_source=user_override" in rec.message and "row_count=100000000" in rec.message
        for rec in caplog.records
    )
