"""Spark-side detection (Stage-1) and Stage-2 filter helpers for the target Delta table.

Both stages serialise each column through ``serialize_column_for_hash`` (the shared
row-hash transform map). Stage-1 (DataFrame path) wraps the rendered Databricks SQL in
``F.expr(...)`` and Stage-2 (SQL filter path) uses it directly, so the two stages cannot
drift, and the target byte stream is identical to the Redshift source serializer and the
row-hash compare path by construction.
"""

import logging

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, LongType

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    SEPARATOR_PYTHON,
    SEPARATOR_SPARK_SQL,
    build_fingerprint_where_clause,
    quote_identifier,
)
from databricks.labs.lakebridge.reconcile.query_builder.expression_generator import serialize_column_for_hash
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect

# rh1/rh2 are 32-bit unsigned values; rh*rh reaches ~2^64 and silently wraps under
# LongType. DecimalType(19, 0) holds 2^32 and Spark's precision rule produces
# DecimalType(38, 0) on the multiply, matching the source-side aggregate type.
RH_OPERAND_TYPE = DecimalType(19, 0)
AGG_TYPE = DecimalType(38, 0)

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
) -> DataFrame:
    """Compute Stage-1 sub-bucket aggregates on the target Delta table.

    Mirrors the source-side detection query: MD5 over concatenated columns, dual-slice
    rh1/rh2 extraction, GROUP BY into sub-buckets with (cnt, p1, p2, p1_rh2, p2_rh2).
    """
    df = spark.table(_table_fqn(catalog, schema, table))

    concat_col = _build_concat_column(columns, column_mapping)
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
) -> str:
    """Build the Spark-SQL subquery that filters target rows for Stage-2 surgical fetch.

    Uses the same column serialisation as ``compute_target_fingerprint`` so the rh1
    values match Stage-1's hashing exactly; ``sub_bucket_count`` must equal Stage-1's.
    """
    concat_expr = _build_target_concat_sql(columns, column_mapping)
    # NOTE (perf, non-blocking): Stage-1 (``_hex_substr_to_long``) deliberately avoids
    # ``CONV`` because it forces a Photon -> JVM ColumnarToRow fallback. Stage-2 uses
    # ``CONV`` here for brevity in the filter subquery. It is a correctness match for
    # Stage-1 (same hex->long value), and the perf cost is bounded because Stage-2 only
    # scans the already-filtered surgical subset, not the full table. Tracked as a perf
    # follow-up; swapping in a SQL-string port of the ASCII-arithmetic path would remove
    # the last CONV without changing the hashed value.
    rh1_expr = f"CAST(CONV(SUBSTR(MD5({concat_expr}), 1, 8), 16, 10) AS BIGINT)"
    sb_expr = f"ABS(MOD({rh1_expr}, {sub_bucket_count}))"
    where_clause = build_fingerprint_where_clause(sb_expr, rh1_expr, solved_hashes, unsolved_sb_ids)
    return f"(SELECT * FROM {_table_fqn(catalog, schema, table)} WHERE {where_clause}) _fp_filtered"


def _build_concat_column(
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
) -> F.Column:
    """Concatenate serialised target columns into a single Column for MD5 hashing."""
    parts = [F.expr(serialize_target_column_sql(_target_col_name(c, column_mapping), c.data_type)) for c in columns]
    if len(parts) == 1:
        return parts[0]
    result = parts[0]
    for part in parts[1:]:
        result = F.concat(result, F.lit(SEPARATOR_PYTHON), part)
    return result


def _build_target_concat_sql(
    columns: list[Schema],
    column_mapping: dict[str, str] | None,
) -> str:
    """SQL-string sibling of ``_build_concat_column`` for the Stage-2 filter subquery."""
    col_exprs = [serialize_target_column_sql(_target_col_name(c, column_mapping), c.data_type) for c in columns]
    if len(col_exprs) == 1:
        return col_exprs[0]
    sep_parts: list[str] = []
    for i, expr in enumerate(col_exprs):
        if i > 0:
            sep_parts.append(SEPARATOR_SPARK_SQL)
        sep_parts.append(expr)
    return f"CONCAT({', '.join(sep_parts)})"


_SPARK_IDENTIFIER_QUOTE = "`"


def quote_spark_identifier(bare: str) -> str:
    """Wrap ``bare`` in Spark SQL's backtick identifier delimiters (see
    ``constants.quote_identifier`` for the escaping + case-preservation rationale)."""
    return quote_identifier(bare, _SPARK_IDENTIFIER_QUOTE)


def serialize_target_column_sql(col_name: str, col_type: str) -> str:
    """Per-column target serializer (Databricks SQL string).

    Routes through the shared row-hash transform map so the target byte stream is
    identical to both the Redshift source serializer and the row-hash compare path by
    construction. Stage-1 (DataFrame) wraps the same string in ``F.expr(...)`` and
    Stage-2 (filter subquery) uses it directly, so the two stages cannot drift.

    The reference is backtick-quoted because ``date_format`` / ``F.expr`` resolve a
    Delta column literally named ``"a.b"`` as a struct path otherwise.
    """
    quoted = quote_spark_identifier(col_name)
    return serialize_column_for_hash(quoted, col_type, get_dialect("databricks"))


def _target_col_name(schema_col: Schema, column_mapping: dict[str, str] | None) -> str:
    """Resolve target physical column name; ``Schema.column_name`` arrives ANSI-delimited."""
    bare = DialectUtils.unnormalize_identifier(schema_col.column_name)
    if column_mapping:
        return column_mapping.get(bare, bare)
    return bare


def _table_fqn(catalog: str | None, schema: str, table: str) -> str:
    """Catalog / schema / table come from ReconcileConfig and are validated
    upstream (Unity Catalog enforces a strict identifier grammar), but routing
    through ``quote_spark_identifier`` makes the FQN robust against names
    containing ``-``, ``.``, or other characters Spark would otherwise treat
    specially. Defensive parity with column-name quoting on this same boundary.
    """
    parts = [quote_spark_identifier(schema), quote_spark_identifier(table)]
    if catalog:
        parts.insert(0, quote_spark_identifier(catalog))
    return ".".join(parts)


def _hash_agg_exprs() -> list[F.Column]:
    rh1_dec19 = F.col("rh1").cast(RH_OPERAND_TYPE)
    rh2_dec19 = F.col("rh2").cast(RH_OPERAND_TYPE)
    rh1_dec38 = F.col("rh1").cast(AGG_TYPE)
    rh2_dec38 = F.col("rh2").cast(AGG_TYPE)
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
