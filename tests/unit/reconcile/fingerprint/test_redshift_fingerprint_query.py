"""Redshift fingerprint SQL builder tests."""

from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.redshift import (  # pylint: disable=import-private-name
    RedshiftFingerprintQueryBuilder,
    _quote_redshift_identifier,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema


def test_concat_uses_source_names_not_target_mapping():
    """Detection concat uses source physical names; target mapping applies on Spark only."""
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
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
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
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
    assert _quote_redshift_identifier("plain") == '"plain"'
    assert _quote_redshift_identifier('we"ird') == '"we""ird"'
    assert _quote_redshift_identifier('""') == '""""""'


def test_serialize_column_strips_ansi_delimiters_and_handles_reserved_word():
    """ANSI-delimited names round-trip into Redshift's double-quoted form.

    Naively wrapping the inbound delimited form in source quotes produces a literal
    column name Redshift rejects. Strip first, re-quote once.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
    serialized = builder.serialize_column("`table`", "integer")
    assert 'CAST("table" AS VARCHAR(65535))' in serialized
    assert "`table`" not in serialized
    assert '"`table`"' not in serialized


def test_serialize_column_boolean_uses_case_when_not_cast_as_varchar():
    """Redshift rejects every BOOLEAN -> string cast form (CAST AS VARCHAR/TEXT, ::TEXT).
    Use CASE WHEN producing lowercase 'true'/'false' so the MD5 stays bit-identical
    with Spark's ``cast(bool AS string)``. NULL flows through ELSE NULL to the outer
    COALESCE sentinel.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
    serialized = builder.serialize_column("`is_priority`", "boolean")

    assert "CAST(" not in serialized.upper().replace(
        "CASE WHEN", ""
    ), f"BOOLEAN must not emit CAST(...): {serialized!r}"
    assert "::TEXT" not in serialized.upper(), f"BOOLEAN must not emit ::TEXT: {serialized!r}"

    assert "CASE WHEN" in serialized
    assert "'true'" in serialized
    assert "'false'" in serialized
    assert "ELSE NULL" in serialized
    assert "COALESCE(" in serialized
    assert '"is_priority"' in serialized
    assert "`is_priority`" not in serialized


def test_serialize_column_non_temporal_non_boolean_uses_cast_as_varchar():
    """Numeric / string / etc. types take the default ``CAST(... AS VARCHAR)`` path.

    BOOLEAN, DATE, TIMESTAMP and TIMESTAMPTZ each take a dedicated branch so
    the byte stream matches the row-hash compare path's ``TO_CHAR(...)``; this
    test pins the inverse — non-temporal types must NOT accidentally route
    through TO_CHAR.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
    for col_type in ("integer", "bigint", "numeric(18,2)", "character varying(64)"):
        serialized = builder.serialize_column("`some_col`", col_type)
        assert 'CAST("some_col" AS VARCHAR(65535))' in serialized, f"{col_type}: {serialized!r}"
        assert "CASE WHEN" not in serialized, f"{col_type}: {serialized!r}"
        assert "AT TIME ZONE" not in serialized, f"{col_type}: {serialized!r}"
        assert "TO_CHAR(" not in serialized, f"{col_type}: {serialized!r}"


def test_serialize_column_timestamptz_uses_to_char_with_fixed_microsecond_format():
    """``TO_CHAR(_ AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')``
    matches the row-hash compare path's per-row format and the Spark target's
    ``DATE_FORMAT(_, 'yyyy-MM-dd HH:mm:ss.SSSSSS')`` byte-for-byte. Bare
    ``CAST(timestamptz AS VARCHAR)`` would emit variable-width fractional seconds
    plus a ``+00`` suffix and silently disagree with both siblings.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)

    for col_type in ("timestamptz", "TIMESTAMPTZ", "timestamp with time zone", "TIMESTAMP WITH TIME ZONE"):
        serialized = builder.serialize_column("`created_at_tz`", col_type)
        assert "AT TIME ZONE 'UTC'" in serialized, f"{col_type}: {serialized!r}"
        assert "TO_CHAR(" in serialized, f"{col_type}: {serialized!r}"
        assert "'YYYY-MM-DD HH24:MI:SS.US'" in serialized, f"{col_type}: {serialized!r}"
        # Forbid the bare cast (variable-width microseconds + ``+00`` suffix).
        assert 'CAST("created_at_tz" AS VARCHAR(65535))' not in serialized
        assert 'CAST("created_at_tz" AT TIME ZONE \'UTC\' AS VARCHAR(65535))' not in serialized

        assert '"created_at_tz"' in serialized
        assert '"`created_at_tz`"' not in serialized
        assert "COALESCE(" in serialized.upper()
        assert "_null_recon_" in serialized


def test_serialize_column_timestamp_without_tz_uses_to_char_yyyy_mm_dd_hh_mi_ss_us():
    """``timestamp`` / ``timestamp without time zone`` take the same
    ``TO_CHAR`` formatter (no ``AT TIME ZONE`` because the value carries no zone).
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    for col_type in ("timestamp", "TIMESTAMP", "timestamp without time zone"):
        serialized = builder.serialize_column("`event_ts`", col_type)
        assert "TO_CHAR(\"event_ts\", 'YYYY-MM-DD HH24:MI:SS.US')" in serialized, f"{col_type}: {serialized!r}"
        assert "AT TIME ZONE" not in serialized, f"{col_type}: {serialized!r}"
        assert 'CAST("event_ts" AS VARCHAR(65535))' not in serialized


def test_serialize_column_date_uses_to_char_yyyy_mm_dd():
    """``date`` formats via ``TO_CHAR(_, 'YYYY-MM-DD')`` to match the
    row-hash compare path and the Spark target's ``DATE_FORMAT(_, 'yyyy-MM-dd')``.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    serialized = builder.serialize_column("`event_dt`", "date")
    assert "TO_CHAR(\"event_dt\", 'YYYY-MM-DD')" in serialized, serialized
    assert 'CAST("event_dt" AS VARCHAR(65535))' not in serialized


def test_serialize_column_timestamptz_respects_treat_empty_as_null():
    """TIMESTAMPTZ wraps with NULLIF when ``treat_empty_as_null=True``, for symmetry with
    the Spark target serializer.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
    serialized = builder.serialize_column("`created_at_tz`", "timestamptz")
    assert "NULLIF(" in serialized.upper()
    assert "COALESCE(NULLIF(" in serialized.upper()


# ---------------------------------------------------------------------------
# Stage-1 <-> Stage-2 whitespace-handling symmetry
# ---------------------------------------------------------------------------
#
# Lakebridge's row-hash compare (``expression_generator`` universal default)
# uses ``COALESCE(TRIM(_), '_null_recon_')`` -- whitespace-insensitive. If the
# fingerprint Stage-1 builder were to emit ``COALESCE(CAST AS VARCHAR,
# '_null_recon_')`` (whitespace-sensitive), the asymmetry would produce a silent
# correctness gap: a row whose only difference is trailing whitespace would be
# flagged by Stage-1 (different MD5) but absorbed by Stage-2 (same SHA2 after
# TRIM), so the row would be fetched then dropped from the recon output.
#
# Contract: TRIM the cast string before COALESCE on both sides.


def test_serialize_column_default_path_pins_max_varchar_width():
    """Default path: ``COALESCE(TRIM(CAST(_ AS VARCHAR(65535))), '_null_recon_')``.

    Bare ``CAST(_ AS VARCHAR)`` in Redshift defaults to ``VARCHAR(256)`` and
    silently truncates anything longer; the Spark target keeps the full string
    so the asymmetry would surface a Stage-1 false-mismatch on every long-text
    row. ``VARCHAR(65535)`` is Redshift's maximum width and matches Spark's
    unbounded string semantics.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    serialized = builder.serialize_column("`amount`", "numeric(18,2)")

    assert serialized == 'COALESCE(TRIM(CAST("amount" AS VARCHAR(65535))), \'_null_recon_\')'
    # Belt-and-braces structural assertions (cheaper to grep on regression):
    assert serialized.startswith("COALESCE(TRIM("), serialized
    assert "CAST(" in serialized
    assert "_null_recon_" in serialized


def test_serialize_column_treat_empty_as_null_keeps_trim_inside_nullif():
    """``treat_empty_as_null=True``: ``COALESCE(NULLIF(TRIM(...), ''), '_null_recon_')``.

    TRIM stays innermost so an all-whitespace value (e.g. ``'   '``) collapses to
    ``''`` and then NULLIF maps it to NULL -- preserving the ``treat_empty_as_null``
    intent while gaining whitespace-insensitive matching.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=True)
    serialized = builder.serialize_column("`name`", "varchar(64)")

    assert serialized == 'COALESCE(NULLIF(TRIM(CAST("name" AS VARCHAR(65535))), \'\'), \'_null_recon_\')'
    assert "NULLIF(TRIM(" in serialized


def test_serialize_column_boolean_emits_trim_around_case_when():
    """BOOLEAN handler stays CASE WHEN producing ``'true'/'false'``; TRIM wraps it.

    TRIM on those literals is a no-op but the structural symmetry with all other
    types matters for review/audit -- there should be exactly one place we decide
    how to wrap the cast expression.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    serialized = builder.serialize_column("`is_priority`", "boolean")

    assert "TRIM(CASE WHEN " in serialized
    assert "'true'" in serialized
    assert "'false'" in serialized
    assert serialized.endswith(", '_null_recon_')")


def test_serialize_column_timestamptz_emits_trim_around_at_time_zone_cast():
    """TIMESTAMPTZ handler produces ``TO_CHAR(_ AT TIME ZONE 'UTC', '...')``;
    TRIM wraps it. TRIM on a timestamp string is a no-op for a well-formed value
    but is still applied for cross-type symmetry.
    """
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    serialized = builder.serialize_column("`created_at_tz`", "timestamptz")

    assert "AT TIME ZONE 'UTC'" in serialized
    assert serialized.endswith(", '_null_recon_')")


def test_serialize_column_redshift_and_spark_share_trim_contract():
    """Source-side TRIM exists in the SQL; target-side TRIM must exist in the Spark
    serializer for the per-row MD5 to be byte-aligned. Cross-checks the contract
    holds at the textual level on the Redshift side; the Spark side is pinned by
    ``test_spark_target_serialization.py``."""
    builder = RedshiftFingerprintQueryBuilder(treat_empty_as_null=False)
    for col_type in ("integer", "varchar(32)", "timestamp without time zone", "boolean", "timestamptz"):
        serialized = builder.serialize_column("`x`", col_type)
        assert "TRIM(" in serialized, f"{col_type}: {serialized!r}"
        assert "_null_recon_" in serialized, f"{col_type}: {serialized!r}"


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
    from pathlib import Path

    from databricks.labs.lakebridge.reconcile.fingerprint.constants import NULL_SENTINEL

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
