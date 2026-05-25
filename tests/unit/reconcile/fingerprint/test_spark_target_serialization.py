"""Pin fingerprint target serialisation to the shared row-hash transform map.

Both Stage-1 (DataFrame) and Stage-2 (SQL filter) route every column through
``serialize_column_for_hash`` so the target byte stream is identical to the Redshift
source serialiser and the row-hash compare path *by construction* — rather than via a
hand-maintained copy that has to be kept in lockstep by these tests.
"""

from __future__ import annotations

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.spark_target import (
    quote_spark_identifier,
    serialize_target_column_sql,
    build_target_filter_subquery,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import serialize_column_for_hash
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

_DATABRICKS = get_dialect("databricks")


@pytest.mark.parametrize(
    "col_type, expected",
    [
        ("string", "COALESCE(TRIM(`notes`), '_null_recon_')"),
        ("int", "COALESCE(TRIM(`notes`), '_null_recon_')"),
        ("timestamp", "COALESCE(DATE_FORMAT(`notes`, 'yyyy-MM-dd HH:mm:ss.SSSSSS'), '_null_recon_')"),
        ("date", "COALESCE(TRIM(`notes`), '_null_recon_')"),
        # DOUBLE is pinned to a fixed-scale DECIMAL string so it matches the Redshift
        # source byte-for-byte (Spark's shortest-round-trip vs Redshift's full-precision
        # default cast would otherwise false-mismatch every double row). NaN / Infinity
        # bypass the DECIMAL cast (which errors on both) and fall back to a direct
        # string cast.
        (
            "double",
            "COALESCE(CASE WHEN ISNAN(`notes`) OR `notes` IN (CAST('Infinity' AS DOUBLE), "
            "CAST('-Infinity' AS DOUBLE)) THEN CAST(`notes` AS STRING) "
            "ELSE CAST(CAST(`notes` AS DECIMAL(38,10)) AS STRING) END, '_null_recon_')",
        ),
    ],
)
def test_target_serializer_matches_shared_transform_map(col_type: str, expected: str) -> None:
    """The target serializer delegates to the shared transform map, so its output equals
    the row-hash compare path for the same column type."""
    sql = serialize_target_column_sql("notes", col_type)
    assert sql == expected
    assert sql == serialize_column_for_hash("`notes`", col_type, _DATABRICKS)


@pytest.mark.parametrize("col_type", ["timestamp", "timestamptz", "timestamp with time zone"])
def test_target_timestamp_renders_plain_date_format_and_matches_rowhash(col_type: str) -> None:
    """The target serializer emits plain ``DATE_FORMAT`` for every timestamp type —
    sqlglot's Databricks dialect maps naive TIMESTAMP and TIMESTAMPTZ to one type,
    so no per-type TZ pin is expressible in the SQL. UTC determinism is enforced at
    the reconcile-session level (``spark.sql.session.timeZone='UTC'`` in
    ``create_recon_dependencies``), while the Redshift source pins TIMESTAMPTZ via
    ``AT TIME ZONE 'UTC'``. The target still inherits the shared transform map so it
    cannot drift from the row-hash compare path."""
    sql = serialize_target_column_sql("event_ts", col_type)
    # No per-column TZ conversion here — determinism is a session invariant.
    assert "CONVERT_TIMEZONE" not in sql, sql
    assert "TO_UTC_TIMESTAMP" not in sql, sql
    assert "DATE_FORMAT(`event_ts`, 'yyyy-MM-dd HH:mm:ss.SSSSSS')" in sql, sql
    assert sql == serialize_column_for_hash("`event_ts`", col_type, _DATABRICKS)


def test_build_target_filter_subquery_serialises_via_transform_map() -> None:
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
    assert "COALESCE(TRIM(`order_id`), '_null_recon_')" in sql, sql
    assert "COALESCE(TRIM(`notes`), '_null_recon_')" in sql, sql
    # Catalog/schema/table route through ``quote_spark_identifier``.
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


def test_build_target_filter_subquery_uses_date_format_for_timestamps() -> None:
    """Stage-2 SQL filter subquery inherits the shared transform map's timestamp
    handling end-to-end: ``timestamp`` renders via ``DATE_FORMAT`` (no UTC pin)."""
    columns = [
        Schema("`order_id`", "int", "`order_id`", "`order_id`"),
        Schema("`event_ts`", "timestamp", "`event_ts`", "`event_ts`"),
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
    assert "DATE_FORMAT(`event_ts`, 'yyyy-MM-dd HH:mm:ss.SSSSSS')" in sql, sql
    assert "TO_UTC_TIMESTAMP" not in sql, sql
    assert "COALESCE(TRIM(`order_id`), '_null_recon_')" in sql, sql


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


def test_serialize_target_column_handles_dot_in_column_name() -> None:
    """Delta column names containing ``.`` must be backtick-escaped so Spark resolves the
    column literally instead of as a struct field path."""
    sql = serialize_target_column_sql("a.b", "string")
    assert "`a.b`" in sql, sql


def test_quote_spark_identifier_doubles_embedded_backtick() -> None:
    """Defense-in-depth: a column name containing a literal backtick must not break the SQL."""
    assert quote_spark_identifier("plain") == "`plain`"
    assert quote_spark_identifier("we`ird") == "`we``ird`"
    # Two embedded backticks → four doubled inside, plus outer pair = six total.
    assert quote_spark_identifier("``") == "``````"


def test_quote_spark_identifier_handles_dot() -> None:
    """``.`` inside an identifier must be wrapped without further treatment — the backtick
    fence is what tells Spark "this is a column name, not a struct path"."""
    assert quote_spark_identifier("a.b") == "`a.b`"
    assert quote_spark_identifier("event.timestamp.utc") == "`event.timestamp.utc`"
