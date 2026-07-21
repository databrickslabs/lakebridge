"""Golden-SQL snapshot tests for the Redshift fingerprint query builder.

``test_redshift_fingerprint_query.py`` covers per-column serialization and
substring invariants. This module locks the *full* emitted SQL skeleton for
``build_detection_sql`` and ``build_source_filter_subquery`` — the aggregate
list, the ``DECIMAL(19,0)``/``DECIMAL(38,0)`` cast placement, the ``CHR(1)``
separator, identifier quoting on schema/table, and the ``GROUP BY`` /
``WHERE`` structure.

The expected strings are assembled from independently-declared literal
building blocks (``_CONCAT``, ``_RH1``, ``_RH2``), *not* imported from the
builder, so any change to the builder's structure — including the planned
migration to sqlglot AST construction — must reproduce byte-identical SQL or
consciously update these goldens. To regenerate after an intentional change,
print the relevant builder method output for ``_COLUMNS`` and paste the new
expected string into the corresponding block below.
"""

from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.redshift import (
    RedshiftFingerprintQueryBuilder,
)
from databricks.labs.lakebridge.reconcile.fingerprint.spark_target import build_target_filter_subquery
from databricks.labs.lakebridge.reconcile.recon_config import Schema

# Canonical 2-column probe: one VARCHAR (TRIM branch) + one BOOLEAN (CASE WHEN
# branch). Small enough to keep the golden readable, but exercises the concat
# separator and both common serialization shapes.
_COLUMNS = [
    Schema("`notes`", "varchar", "`notes`", '"notes"'),
    Schema("`is_priority`", "boolean", "`is_priority`", '"is_priority"'),
]
_SUB_BUCKET_COUNT = 2048
_BUCKET_COUNT = 64

# --- Literal building blocks (declared independently of the builder) --------
_CONCAT = (
    "COALESCE(TRIM(\"notes\"), '_null_recon_')"
    + " || CHR(1) || "
    + "COALESCE(CASE WHEN \"is_priority\" THEN 'true' WHEN NOT \"is_priority\" THEN 'false' ELSE NULL END,"
    + " '_null_recon_')"
)
_RH1 = f"STRTOL(SUBSTRING(MD5({_CONCAT}), 1, 8), 16)"
_RH2 = f"STRTOL(SUBSTRING(MD5({_CONCAT}), 9, 8), 16)"
_SB_EXPR = f"ABS(MOD({_RH1}, {_SUB_BUCKET_COUNT}))"
_BUCKET_EXPR = f"ABS(MOD({_RH1}, {_BUCKET_COUNT}))"

_EXPECTED_DETECTION = (
    f"SELECT {_SB_EXPR} AS sub_bucket_id, "
    f"{_BUCKET_EXPR} AS bucket_id, "
    f"COUNT(*) AS cnt, "
    f"SUM(CAST({_RH1} AS DECIMAL(38,0))) AS p1, "
    f"SUM(CAST({_RH1} AS DECIMAL(19,0)) * CAST({_RH1} AS DECIMAL(19,0))) AS p2, "
    f"SUM(CAST({_RH2} AS DECIMAL(38,0))) AS p1_rh2, "
    f"SUM(CAST({_RH2} AS DECIMAL(19,0)) * CAST({_RH2} AS DECIMAL(19,0))) AS p2_rh2 "
    f'FROM "public"."orders" '
    f"GROUP BY sub_bucket_id, bucket_id"
)


def test_build_detection_sql_golden() -> None:
    builder = RedshiftFingerprintQueryBuilder()
    sql = builder.build_detection_sql(
        schema="public",
        table="orders",
        columns=_COLUMNS,
        column_mapping=None,
        sub_bucket_count=_SUB_BUCKET_COUNT,
        bucket_count=_BUCKET_COUNT,
    )
    assert sql == _EXPECTED_DETECTION


def test_build_detection_sql_ignores_column_mapping_on_source() -> None:
    """The source side reads Redshift physical names; a column_mapping must not
    alter the emitted SQL. Locked at the full-SQL level so a future change can't
    silently leak target names into the source scan."""
    builder = RedshiftFingerprintQueryBuilder()
    mapped = builder.build_detection_sql(
        schema="public",
        table="orders",
        columns=_COLUMNS,
        column_mapping={"notes": "tgt_notes", "is_priority": "tgt_priority"},
        sub_bucket_count=_SUB_BUCKET_COUNT,
        bucket_count=_BUCKET_COUNT,
    )
    assert mapped == _EXPECTED_DETECTION


def test_build_source_filter_subquery_golden() -> None:
    builder = RedshiftFingerprintQueryBuilder()
    sql = builder.build_source_filter_subquery(
        schema="public",
        table="orders",
        columns=_COLUMNS,
        sub_bucket_count=_SUB_BUCKET_COUNT,
        solved_hashes={5: [111, 222]},
        unsolved_sb_ids=[7],
    )
    expected_where = f"({_SB_EXPR} IN (5) AND {_RH1} IN (111, 222)) OR {_SB_EXPR} IN (7)"
    expected = f'(SELECT * FROM "public"."orders" WHERE {expected_where}) _fp_filtered'
    assert sql == expected


def test_build_concat_expression_golden() -> None:
    builder = RedshiftFingerprintQueryBuilder()
    assert builder.build_concat_expression(_COLUMNS) == _CONCAT


# --- Target (Spark) side ----------------------------------------------------
# Structurally different from the Redshift source (CHAR(1)/CONCAT/CONV vs
# CHR(1)/||/STRTOL, backtick vs double-quote identifiers) but byte-equivalent
# by the shared transform map: BOOLEAN serialises via TRIM on Spark, whose
# implicit string cast yields the same lowercase 'true'/'false' the source's
# CASE WHEN emits. This golden locks the Stage-2 target filter skeleton.
_TGT_CONCAT = (
    "CONCAT("
    + "COALESCE(TRIM(`notes`), '_null_recon_'), "
    + "CHAR(1), "
    + "COALESCE(TRIM(`is_priority`), '_null_recon_'))"
)
_TGT_RH1 = f"CAST(CONV(SUBSTR(MD5({_TGT_CONCAT}), 1, 8), 16, 10) AS BIGINT)"
_TGT_SB_EXPR = f"ABS(MOD({_TGT_RH1}, {_SUB_BUCKET_COUNT}))"


def test_build_target_filter_subquery_golden() -> None:
    sql = build_target_filter_subquery(
        "main",
        "perf_test",
        "orders",
        _COLUMNS,
        None,
        {5: [111, 222]},
        [7],
        sub_bucket_count=_SUB_BUCKET_COUNT,
    )
    expected_where = f"({_TGT_SB_EXPR} IN (5) AND {_TGT_RH1} IN (111, 222)) OR {_TGT_SB_EXPR} IN (7)"
    expected = f"(SELECT * FROM `main`.`perf_test`.`orders` WHERE {expected_where}) _fp_filtered"
    assert sql == expected


def test_detection_sql_quotes_exotic_schema_and_table() -> None:
    """Schema/table flow through the same quoting helper as columns, so a name
    carrying a stray double-quote is escaped (doubled) rather than malforming
    the SQL."""
    builder = RedshiftFingerprintQueryBuilder()
    sql = builder.build_detection_sql(
        schema='my"schema',
        table="my-table",
        columns=_COLUMNS,
        column_mapping=None,
        sub_bucket_count=_SUB_BUCKET_COUNT,
        bucket_count=_BUCKET_COUNT,
    )
    assert 'FROM "my""schema"."my-table"' in sql
