"""Spark-side detection (Stage-1) and Stage-2 filter helpers for the target Delta table.

Stage-1 (DataFrame path) and Stage-2 (SQL filter path) share one column-serialisation
contract here: ``COALESCE(TRIM(CAST(_ AS string)), '<sentinel>')``. Keeping both helpers
in this module prevents the two stages from drifting silently (the row would be flagged
by Stage-1 and then dropped from Stage-2 when the per-row SHA2 inputs disagreed).
"""

import logging

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, LongType

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    NULL_SENTINEL,
    SEPARATOR_PYTHON,
    SEPARATOR_SPARK_SQL,
    build_fingerprint_where_clause,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema

# rh1/rh2 are 32-bit unsigned values; rh*rh reaches ~2^64 and silently wraps under
# LongType. DecimalType(19, 0) holds 2^32 and Spark's precision rule produces
# DecimalType(38, 0) on the multiply, matching the source-side aggregate type.
_RH_OPERAND_TYPE = DecimalType(19, 0)
_AGG_TYPE = DecimalType(38, 0)

logger = logging.getLogger(__name__)


def compute_target_fingerprint(
    spark: SparkSession,
    catalog: str | None,
    schema: str,
    table: str,
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
    sub_bucket_count: int,
    bucket_count: int,
    treat_empty_as_null: bool = False,
) -> DataFrame:
    """Compute Stage-1 sub-bucket aggregates on the target Delta table.

    Mirrors the source-side detection query: MD5 over concatenated columns, dual-slice
    rh1/rh2 extraction, GROUP BY into sub-buckets with (cnt, p1, p2, p1_rh2, p2_rh2).
    """
    df = spark.table(_table_fqn(catalog, schema, table))

    concat_col = _build_concat_column(columns, column_mapping, treat_empty_as_null)
    md5_col = F.md5(concat_col)

    rh1_col = _hex_substr_to_long(md5_col, 1, 8)
    rh2_col = _hex_substr_to_long(md5_col, 9, 8)

    df_hashed = df.select(
        F.abs(rh1_col % F.lit(sub_bucket_count)).alias("sub_bucket_id"),
        F.abs(rh1_col % F.lit(bucket_count)).alias("bucket_id"),
        rh1_col.alias("rh1"),
        rh2_col.alias("rh2"),
    )
    return df_hashed.groupBy("sub_bucket_id", "bucket_id").agg(*_hash_agg_exprs())


def build_target_filter_subquery(
    catalog: str | None,
    schema: str,
    table: str,
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
    solved_hashes: dict[int, list[int]],
    unsolved_sb_ids: list[int],
    *,
    sub_bucket_count: int,
    treat_empty_as_null: bool = False,
) -> str:
    """Build the Spark-SQL subquery that filters target rows for Stage-2 surgical fetch.

    Uses the same column serialisation as ``compute_target_fingerprint`` so the rh1
    values match Stage-1's hashing exactly; ``sub_bucket_count`` must equal Stage-1's.
    """
    concat_expr = _build_target_concat_sql(columns, column_mapping, treat_empty_as_null)
    rh1_expr = f"CAST(CONV(SUBSTR(MD5({concat_expr}), 1, 8), 16, 10) AS BIGINT)"
    sb_expr = f"ABS(MOD({rh1_expr}, {sub_bucket_count}))"
    where_clause = build_fingerprint_where_clause(sb_expr, rh1_expr, solved_hashes, unsolved_sb_ids)
    return f"(SELECT * FROM {_table_fqn(catalog, schema, table)} WHERE {where_clause}) _fp_filtered"


def _build_concat_column(
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
    treat_empty_as_null: bool,
) -> F.Column:
    """Concatenate serialised target columns into a single Column for MD5 hashing."""
    parts = [
        _serialize_column_spark(_target_col_name(c, column_mapping), c.data_type, treat_empty_as_null) for c in columns
    ]
    if len(parts) == 1:
        return parts[0]
    result = parts[0]
    for part in parts[1:]:
        result = F.concat(result, F.lit(SEPARATOR_PYTHON), part)
    return result


def _build_target_concat_sql(
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
    treat_empty_as_null: bool,
) -> str:
    """SQL-string sibling of ``_build_concat_column`` for the Stage-2 filter subquery."""
    col_exprs = [
        _serialize_column_spark_sql(_target_col_name(c, column_mapping), c.data_type, treat_empty_as_null)
        for c in columns
    ]
    if len(col_exprs) == 1:
        return col_exprs[0]
    sep_parts: list[str] = []
    for i, expr in enumerate(col_exprs):
        if i > 0:
            sep_parts.append(SEPARATOR_SPARK_SQL)
        sep_parts.append(expr)
    return f"CONCAT({', '.join(sep_parts)})"


# Spark types whose default ``cast(_ AS string)`` representation drifts from the
# Redshift source-side ``TO_CHAR(...)`` payload. Listing the bare type prefix
# (lowercased) is enough — Spark's ``data_type`` from INFORMATION_SCHEMA already
# strips precision modifiers for these.
#
# We classify timestamps into two families: timezone-aware (LTZ — Spark's default
# ``timestamp``, plus ``timestamp_ltz`` and ``timestamp with time zone``) and
# timezone-naive (``timestamp_ntz`` and ``timestamp without time zone``).
# Stage-1 must produce the same bytes as the Redshift source for a given
# logical row, and Redshift renders ``timestamptz`` ``AT TIME ZONE 'UTC'``.
# A TZ-aware Spark column rendered via ``date_format`` uses the session
# timezone — if a cluster runs with a non-UTC ``spark.sql.session.timeZone``
# the two sides emit different bytes for the same instant. We therefore
# normalise TZ-aware columns to the UTC wall-clock before formatting.
_SPARK_TIMESTAMP_NTZ_TOKENS = ("timestamp_ntz", "timestamp without time zone")


def _classify_timestamp(col_type: str) -> str | None:
    """Return ``"ltz"``, ``"ntz"``, or ``None`` for non-timestamp columns."""
    if not col_type.startswith("timestamp"):
        return None
    if any(col_type.startswith(p) for p in _SPARK_TIMESTAMP_NTZ_TOKENS):
        return "ntz"
    return "ltz"


def _serialize_column_spark(col_name: str, col_type: str, treat_empty_as_null: bool) -> F.Column:
    """Stage-1 (DataFrame) per-column serializer.

    Four contracts coexist here:
      * ``TRIM`` keeps Stage-1 whitespace-symmetric with Stage-2 (otherwise a
        row whose only difference is trailing whitespace surfaces in Stage-1
        and is silently dropped by Stage-2's per-row SHA2).
      * Timestamps and dates route through ``DATE_FORMAT`` so the byte stream
        matches the row-hash compare path's
        ``DATE_FORMAT(_, 'yyyy-MM-dd HH:mm:ss.SSSSSS')`` and the source-side
        Redshift ``TO_CHAR(_, 'YYYY-MM-DD HH24:MI:SS.US')``. Default
        ``cast(_ AS string)`` produces variable-width fractional seconds.
      * TZ-aware (LTZ) timestamps are shifted to the UTC wall-clock via
        ``TO_UTC_TIMESTAMP(_, CURRENT_TIMEZONE())`` before formatting so a
        cluster running with a non-UTC session timezone still emits bytes
        identical to Redshift's ``TO_CHAR(_ AT TIME ZONE 'UTC', _)``. Without
        this pin, the same instant would render differently on the two sides
        and Stage-1 would over-report mismatches on every TZ-aware column.
      * The column reference is built via ``F.expr(_quote_spark_identifier(...))``
        because ``F.col`` interprets ``.`` as a struct path — Delta columns
        literally named ``"a.b"`` would otherwise fail to resolve.
    """
    col_type_lower = (col_type or "").strip().lower()
    spark_col = F.expr(_quote_spark_identifier(col_name))
    ts_kind = _classify_timestamp(col_type_lower)
    if ts_kind == "ltz":
        ts_in_utc = F.to_utc_timestamp(spark_col, F.current_timezone())
        cast_col = F.trim(F.date_format(ts_in_utc, "yyyy-MM-dd HH:mm:ss.SSSSSS"))
    elif ts_kind == "ntz":
        cast_col = F.trim(F.date_format(spark_col, "yyyy-MM-dd HH:mm:ss.SSSSSS"))
    elif col_type_lower == "date":
        cast_col = F.trim(F.date_format(spark_col, "yyyy-MM-dd"))
    else:
        cast_col = F.trim(spark_col.cast("string"))
    if treat_empty_as_null:
        return F.coalesce(
            F.when(cast_col == F.lit(""), None).otherwise(cast_col),
            F.lit(NULL_SENTINEL),
        )
    return F.coalesce(cast_col, F.lit(NULL_SENTINEL))


_SPARK_IDENTIFIER_QUOTE = "`"


def _quote_spark_identifier(bare: str) -> str:
    """Wrap ``bare`` in Spark SQL's backtick identifier delimiters, doubling any embedded
    backtick. Defense-in-depth: today's values come from Delta metadata and never carry
    a backtick, but ``recon_capture`` scrubs persisted string values for the same reason
    — both boundaries should be consistent.
    """
    escaped = bare.replace(_SPARK_IDENTIFIER_QUOTE, _SPARK_IDENTIFIER_QUOTE * 2)
    return f"{_SPARK_IDENTIFIER_QUOTE}{escaped}{_SPARK_IDENTIFIER_QUOTE}"


def _serialize_column_spark_sql(col_name: str, col_type: str, treat_empty_as_null: bool) -> str:
    """Stage-2 (SQL string) per-column serializer; must produce hashes identical to ``_serialize_column_spark``."""
    col_type_lower = (col_type or "").strip().lower()
    quoted = _quote_spark_identifier(col_name)
    ts_kind = _classify_timestamp(col_type_lower)
    if ts_kind == "ltz":
        cast_expr = f"TRIM(DATE_FORMAT(TO_UTC_TIMESTAMP({quoted}, CURRENT_TIMEZONE()), 'yyyy-MM-dd HH:mm:ss.SSSSSS'))"
    elif ts_kind == "ntz":
        cast_expr = f"TRIM(DATE_FORMAT({quoted}, 'yyyy-MM-dd HH:mm:ss.SSSSSS'))"
    elif col_type_lower == "date":
        cast_expr = f"TRIM(DATE_FORMAT({quoted}, 'yyyy-MM-dd'))"
    else:
        cast_expr = f"TRIM(CAST({quoted} AS STRING))"
    if treat_empty_as_null:
        return f"COALESCE(NULLIF({cast_expr}, ''), '{NULL_SENTINEL}')"
    return f"COALESCE({cast_expr}, '{NULL_SENTINEL}')"


def _target_col_name(schema_col: Schema, column_mapping: dict[str, str] | None) -> str:
    """Resolve target physical column name; ``Schema.column_name`` arrives ANSI-delimited."""
    bare = DialectUtils.unnormalize_identifier(schema_col.column_name)
    if column_mapping:
        return column_mapping.get(bare, bare)
    return bare


def _table_fqn(catalog: str | None, schema: str, table: str) -> str:
    """Catalog / schema / table come from ReconcileConfig and are validated
    upstream (Unity Catalog enforces a strict identifier grammar), but routing
    through ``_quote_spark_identifier`` makes the FQN robust against names
    containing ``-``, ``.``, or other characters Spark would otherwise treat
    specially. Defensive parity with column-name quoting on this same boundary.
    """
    parts = [_quote_spark_identifier(schema), _quote_spark_identifier(table)]
    if catalog:
        parts.insert(0, _quote_spark_identifier(catalog))
    return ".".join(parts)


def _hash_agg_exprs() -> list[F.Column]:
    rh1_dec19 = F.col("rh1").cast(_RH_OPERAND_TYPE)
    rh2_dec19 = F.col("rh2").cast(_RH_OPERAND_TYPE)
    rh1_dec38 = F.col("rh1").cast(_AGG_TYPE)
    rh2_dec38 = F.col("rh2").cast(_AGG_TYPE)
    return [
        F.count("*").alias("cnt"),
        F.sum(rh1_dec38).alias("p1"),
        F.sum(rh1_dec19 * rh1_dec19).alias("p2"),
        F.sum(rh2_dec38).alias("p1_rh2"),
        F.sum(rh2_dec19 * rh2_dec19).alias("p2_rh2"),
    ]


def _hex_substr_to_long(md5_col: F.Column, start: int, length: int) -> F.Column:
    """Convert an MD5 hex substring to a long via Photon-safe ASCII arithmetic.

    F.conv() forces ColumnarToRow JVM fallback on Photon; substring/ascii/when-otherwise/
    multiply/add are all Photon-native. md5() returns lowercase hex so no upper() needed.
    """
    hex_slice = F.substring(md5_col, start, length)
    ascii_0 = F.ascii(F.lit("0"))
    ascii_a = F.ascii(F.lit("a"))
    result = F.lit(0).cast(LongType())
    for i in range(length):
        char_col = F.substring(hex_slice, i + 1, 1)
        digit = (
            F.when(F.ascii(char_col) >= ascii_a, F.ascii(char_col) - ascii_a + F.lit(10))
            .otherwise(F.ascii(char_col) - ascii_0)
            .cast(LongType())
        )
        result = result * F.lit(16).cast(LongType()) + digit
    return result
