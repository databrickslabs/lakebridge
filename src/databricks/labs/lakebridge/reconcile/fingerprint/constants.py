"""Shared constants and SQL helpers for fingerprint detection and row filtering."""

from __future__ import annotations

# Must match the row-hash path's NULL stand-in (``'_null_recon_'`` literal in
# ``reconcile/query_builder/expression_generator.py``). Fingerprint and row-hash
# both encode NULLs into the per-column hash payload before MD5/SHA — picking a
# different stand-in here would alias real data ``'_null_recon_'`` with NULL on
# only one side and produce the inverse alias on the other, so any row that
# happens to carry either literal would be silently misclassified by Stage-1.
# A unit test pins this to the row-hash literal so a future drift fails CI
# rather than the reconcile.
NULL_SENTINEL = "_null_recon_"

# chr(1) — column separator inside the MD5 concat. Rendered three ways:
#   Redshift SQL:           CHR(1)
#   Spark SQL:              CHAR(1)
#   Python / Spark DataFrame: "\x01"
SEPARATOR_PYTHON = "\x01"
SEPARATOR_REDSHIFT_SQL = "CHR(1)"
SEPARATOR_SPARK_SQL = "CHAR(1)"

# Static defaults retained for backwards compatibility and as the fallback when the
# adaptive selector has no row count to work with.
SUB_BUCKET_COUNT = 1_048_576  # 1M sub-buckets
BUCKET_COUNT = 32_768

# Adaptive tier table. Each entry: (max_row_count_inclusive, sub_bucket_count, bucket_count).
# Last entry's max_row_count is None and clamps everything larger. Sub-bucket counts are
# powers of 2 to keep MOD distribution clean; bucket count = sub_bucket_count / 1024.
SUB_BUCKET_TIERS: tuple[tuple[int | None, int, int], ...] = (
    (50_000, 16_384, 128),  # < 50K
    (500_000, 262_144, 512),  # 50K – 500K
    (50_000_000, 1_048_576, 1_024),  # 500K – 50M
    (500_000_000, 2_097_152, 2_048),  # 50M – 500M
    (5_000_000_000, 4_194_304, 4_096),  # 500M – 5B
    (50_000_000_000, 8_388_608, 8_192),  # 5B – 50B
    (None, 16_777_216, 16_384),  # 50B+
)


def pick_sub_bucket_count(row_count: int | None) -> tuple[int, int]:
    """Select (sub_bucket_count, bucket_count) for ``row_count``.

    Falls back to (SUB_BUCKET_COUNT, BUCKET_COUNT) when the count is unknown or
    non-positive, so callers can pass None safely.

    >>> pick_sub_bucket_count(10_000)
    (16384, 128)
    >>> pick_sub_bucket_count(100_000_000)
    (2097152, 2048)
    >>> pick_sub_bucket_count(None)
    (1048576, 32768)
    """
    if row_count is None or row_count <= 0:
        return SUB_BUCKET_COUNT, BUCKET_COUNT
    for max_row_count, sub_buckets, buckets in SUB_BUCKET_TIERS:
        if max_row_count is None or row_count <= max_row_count:
            return sub_buckets, buckets
    return SUB_BUCKET_COUNT, BUCKET_COUNT


def build_fingerprint_where_clause(
    sb_expr: str,
    rh1_expr: str,
    solved_hashes: dict[int, list[int]],
    unsolved_sb_ids: list[int],
) -> str:
    """Build the WHERE body (no ``WHERE``, no trailing alias) for a filtered fetch.

    Emits the union form ``(sb_expr IN (..) AND rh1_expr IN (..)) [OR sb_expr IN (..)]``.
    The form is mathematically equivalent to per-sub-bucket disjuncts because
    ``sb_id = ABS(MOD(rh1, N))`` is invariant, but stays ``O(|sb_expr| + |IN list|)``
    instead of ``O(k · |sb_expr|)`` so it stays under Redshift's 16 MB statement
    limit even on workloads with millions of solved sub-buckets.

    Raises ``ValueError`` when both filter inputs are empty: callers must gate the
    fetch (eligibility check in the orchestrator) before reaching this helper. An
    empty result here would interpolate to ``WHERE )`` downstream — fail-loud beats
    silently emitting a syntactically broken query that fail-open would mask.
    """
    if not solved_hashes and not unsolved_sb_ids:
        raise ValueError(
            "build_fingerprint_where_clause requires at least one of solved_hashes "
            "or unsolved_sb_ids to be non-empty; the empty case must be filtered "
            "out by the caller before issuing a fetch."
        )
    conditions: list[str] = []
    # Sort all IN-list operands for deterministic SQL across dict / list iteration
    # orders — helps query-plan caching and unit-test diffing.
    if solved_hashes:
        sb_list = ", ".join(str(sb_id) for sb_id in sorted(solved_hashes))
        hash_list = ", ".join(str(h) for h in sorted({h for hs in solved_hashes.values() for h in hs}))
        conditions.append(f"({sb_expr} IN ({sb_list}) AND {rh1_expr} IN ({hash_list}))")
    if unsolved_sb_ids:
        sb_list = ", ".join(str(sb_id) for sb_id in sorted(unsolved_sb_ids))
        conditions.append(f"{sb_expr} IN ({sb_list})")
    return " OR ".join(conditions)
