"""Redshift fingerprint SQL builder tests."""

from pathlib import Path

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.constants import NULL_SENTINEL
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.redshift import (
    RedshiftFingerprintQueryBuilder,
    quote_redshift_identifier,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import serialize_column_for_hash
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

_REDSHIFT = get_dialect("redshift")


def test_concat_uses_source_names_not_target_mapping():
    """Detection concat uses source physical names; target mapping applies on Spark only."""
    builder = RedshiftFingerprintQueryBuilder()
    cols = [
        Schema("`src_a`", "varchar", "`src_a`", '"src_a"'),
        Schema("`src_b`", "int", "`src_b`", '"src_b"'),
    ]
    sql = builder.build_concat_expression(cols)
    assert '"src_a"' in sql
    assert '"src_b"' in sql
    assert '"`src_a`"' not in sql, f"Doubly-quoted column reference in {sql!r}"
    assert "tgt_a" not in sql
    assert "tgt_b" not in sql


def test_double_column_normalised_to_fixed_scale_decimal_matching_target():
    """Redshift ``double precision`` and Databricks ``DOUBLE`` serialise to different
    strings under the universal TRIM default (full-precision vs shortest round-trip),
    which false-mismatches every double row. Both sides pin to a fixed-scale
    ``DECIMAL(38,10)`` string so the source concat matches the Spark target byte stream.
    """
    builder = RedshiftFingerprintQueryBuilder()
    cols = [Schema("`carat`", "double precision", "`carat`", '"carat"')]

    sql = builder.build_concat_expression(cols)

    assert 'CAST(CAST("carat" AS DECIMAL(38,10)) AS VARCHAR)' in sql, sql
    assert "TRIM" not in sql, sql
    # The source concat delegates to the same shared transform map the row-hash path uses.
    assert serialize_column_for_hash('"carat"', "double precision", _REDSHIFT) in sql


def test_source_filter_subquery_uses_detection_columns():
    """Filter subquery WHERE uses MD5 over the same detection columns."""
    builder = RedshiftFingerprintQueryBuilder()
    detection_cols = [
        Schema("`a`", "int", "`a`", '"a"'),
        Schema("`join_key`", "int", "`join_key`", '"join_key"'),
    ]
    sql = builder.build_source_filter_subquery(
        schema="public",
        table="t",
        columns=detection_cols,
        sub_bucket_count=1024,
        solved_hashes={0: [1]},
        unsolved_sb_ids=[],
    )
    assert '"a"' in sql
    assert '"join_key"' in sql
    assert '"`a`"' not in sql
    assert 'FROM "public"."t"' in sql
    assert "MD5" in sql
    assert "_fp_filtered" in sql


def test_detection_sql_uses_decimal_precision_for_hash_aggregates():
    """Cast rh*rh operands to DECIMAL(19,0) so the product is DECIMAL(38,0); SUM lifts
    linear rh aggregates to DECIMAL(38,0). BIGINT in the multiply would overflow.
    """
    builder = RedshiftFingerprintQueryBuilder()
    cols = [Schema("`a`", "int", "`a`", '"a"')]

    sql = builder.build_detection_sql(
        schema="public",
        table="t",
        columns=cols,
        column_mapping=None,
        sub_bucket_count=1024,
        bucket_count=8192,
    )

    assert "CAST(STRTOL(SUBSTRING(MD5" in sql
    assert "AS DECIMAL(38,0))" in sql
    assert "AS DECIMAL(19,0))" in sql
    assert "AS BIGINT) * CAST(" not in sql, f"BIGINT in rh*rh overflows on Redshift: {sql!r}"


def test_quote_redshift_identifier_doubles_embedded_double_quote():
    """Defense-in-depth: a column name containing a literal ``"`` must not break the SQL."""
    assert quote_redshift_identifier("plain") == '"plain"'
    assert quote_redshift_identifier('we"ird') == '"we""ird"'
    assert quote_redshift_identifier('""') == '""""""'


def test_serialize_column_strips_ansi_delimiters_and_handles_reserved_word():
    """ANSI-delimited names round-trip into Redshift's double-quoted form.

    Naively wrapping the inbound delimited form in source quotes produces a literal
    column name Redshift rejects. Strip first, re-quote once.
    """
    builder = RedshiftFingerprintQueryBuilder()
    serialized = builder.serialize_column("`table`", "integer")
    assert '"table"' in serialized
    assert "`table`" not in serialized
    assert '"`table`"' not in serialized


@pytest.mark.parametrize(
    "col_type, expected",
    [
        ("integer", 'COALESCE(TRIM("amount"), \'_null_recon_\')'),
        ("numeric(18,2)", 'COALESCE(TRIM("amount"), \'_null_recon_\')'),
        ("varchar(64)", 'COALESCE(TRIM("amount"), \'_null_recon_\')'),
        ("timestamp", 'COALESCE(TO_CHAR("amount", \'YYYY-MM-DD HH24:MI:SS.US\'), \'_null_recon_\')'),
        (
            "timestamptz",
            'COALESCE(TO_CHAR("amount" AT TIME ZONE \'UTC\', \'YYYY-MM-DD HH24:MI:SS.US\'), \'_null_recon_\')',
        ),
        ("date", 'COALESCE(TO_CHAR("amount", \'YYYY-MM-DD\'), \'_null_recon_\')'),
    ],
)
def test_serialize_column_matches_shared_transform_map(col_type: str, expected: str) -> None:
    """The fingerprint source serializer delegates to the shared row-hash transform map,
    so its output is byte-identical to the row-hash compare path for every type."""
    builder = RedshiftFingerprintQueryBuilder()
    serialized = builder.serialize_column("`amount`", col_type)
    assert serialized == expected
    assert serialized == serialize_column_for_hash('"amount"', col_type, _REDSHIFT)


def test_serialize_column_timestamptz_pins_utc():
    """TIMESTAMPTZ must pin ``AT TIME ZONE 'UTC'`` so the source render is
    independent of the Redshift session ``TIMEZONE``. Without the pin a non-UTC
    Redshift session and a UTC Spark session would make every TIMESTAMPTZ row
    false-mismatch. The shared transform map keeps this identical to the row-hash
    compare path, and the Databricks side pins the same instant via
    ``CONVERT_TIMEZONE('UTC', ...)``.
    """
    builder = RedshiftFingerprintQueryBuilder()
    for col_type in ("timestamptz", "TIMESTAMPTZ", "timestamp with time zone"):
        serialized = builder.serialize_column("`created_at_tz`", col_type)
        assert "AT TIME ZONE 'UTC'" in serialized, f"{col_type}: {serialized!r}"
        assert (
            "TO_CHAR(\"created_at_tz\" AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')" in serialized
        ), f"{col_type}: {serialized!r}"


def test_serialize_column_boolean_uses_case_when_not_cast_as_varchar():
    """Redshift rejects every BOOLEAN -> string cast form (CAST AS VARCHAR/TEXT, ::TEXT).
    The shared transform map's Redshift BOOLEAN handler uses CASE WHEN producing lowercase
    ``'true'/'false'`` so the MD5 stays bit-identical with Spark's ``cast(bool AS string)``.
    """
    builder = RedshiftFingerprintQueryBuilder()
    serialized = builder.serialize_column("`is_priority`", "boolean")

    assert "CAST(" not in serialized.upper(), f"BOOLEAN must not emit CAST(...): {serialized!r}"
    assert "::TEXT" not in serialized.upper(), f"BOOLEAN must not emit ::TEXT: {serialized!r}"
    assert "CASE WHEN" in serialized
    assert "'true'" in serialized
    assert "'false'" in serialized
    assert "ELSE NULL" in serialized
    assert "COALESCE(" in serialized
    assert '"is_priority"' in serialized
    assert "`is_priority`" not in serialized


def test_serialize_column_source_and_target_share_null_sentinel():
    """Source (Redshift) and target (Databricks) serializers both terminate in the same
    COALESCE sentinel so a NULL on either side hashes to the same byte stream."""
    builder = RedshiftFingerprintQueryBuilder()
    for col_type in ("integer", "varchar(32)", "timestamp", "boolean", "timestamptz", "date"):
        serialized = builder.serialize_column("`x`", col_type)
        assert serialized.endswith(", '_null_recon_')"), f"{col_type}: {serialized!r}"


def test_null_sentinel_parity_with_row_hash_path():
    """The fingerprint NULL stand-in must match the row-hash literal in
    ``expression_generator``. Drift here aliases real data ``'_null_recon_'``
    with NULL on only the fingerprint side, so any row that carries that literal
    is silently misclassified during Stage-1 sub-bucket aggregation.

    This guard is intentionally a string-literal grep against the source file
    rather than an import: if a refactor moves the row-hash sentinel to a named
    constant, the grep failure points to *both* call sites in one CI run instead
    of letting the import succeed against a renamed but mismatched value.
    """
    expr_gen = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "databricks"
        / "labs"
        / "lakebridge"
        / "reconcile"
        / "query_builder"
        / "expression_generator.py"
    )
    if not expr_gen.exists():
        # Repo layout fallback for installed-package tests.
        expr_gen = (
            Path(__file__).resolve().parents[4]
            / "src"
            / "databricks"
            / "labs"
            / "lakebridge"
            / "reconcile"
            / "query_builder"
            / "expression_generator.py"
        )
    text = expr_gen.read_text(encoding="utf-8")
    assert f"'{NULL_SENTINEL}'" in text, (
        f"fingerprint NULL_SENTINEL={NULL_SENTINEL!r} not found in row-hash "
        f"expression_generator.py — sentinels have drifted; rows whose data "
        f"contains {NULL_SENTINEL!r} would be silently misclassified."
    )
