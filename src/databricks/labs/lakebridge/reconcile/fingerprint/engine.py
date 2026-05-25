import logging
import math
from dataclasses import dataclass, field
from typing import Literal

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

DetectionVerdict = Literal["MATCH", "MISMATCH"]

logger = logging.getLogger(__name__)

_SYSTEMIC_GUARD_THRESHOLD = 0.15
# Driver-OOM guard: collecting millions of mismatched sub-buckets is unsafe.
_MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE = 50_000
# MD5 8-hex extraction yields 32-bit unsigned values in [0, 0xFFFFFFFF].
_MAX_RH_VALUE = 0xFFFFFFFF


@dataclass
class SolveResult:
    sub_bucket_id: int
    source_hashes: list[int]
    target_hashes: list[int]


@dataclass
class DetectionResult:
    verdict: DetectionVerdict
    solved_results: list[SolveResult] = field(default_factory=list)
    unsolved_sb_ids: list[int] = field(default_factory=list)
    total_mismatched_sbs: int = 0
    systemic_mismatch: bool = False


def detect_and_solve(
    source_agg_df: DataFrame,
    target_agg_df: DataFrame,
) -> DetectionResult:
    """Compare source and target sub-bucket aggregates and solve for culprit hashes.

    Returns a DetectionResult with verdict, solved hashes, and unsolved sub-bucket IDs
    for brute-force fetch.

    Stage-1 materialization: the joined aggregate is cached before any action so
    the agg + the subsequent ``mismatched.collect()`` reuse the same physical
    rows instead of re-pulling from Redshift / Delta. The cache is released on
    every return path. Without this cache the function would fire three separate
    Spark jobs (joined.count, mismatched.count, mismatched.collect), each
    re-evaluating the Stage-1 read; with it, two jobs run (one agg + one
    collect) and both read the cached frame.
    """
    joined = (
        source_agg_df.alias("src")
        .join(
            target_agg_df.alias("tgt"),
            on=["sub_bucket_id", "bucket_id"],
            how="full",
        )
        .select(
            F.coalesce(F.col("src.sub_bucket_id"), F.col("tgt.sub_bucket_id")).alias("sub_bucket_id"),
            F.coalesce(F.col("src.bucket_id"), F.col("tgt.bucket_id")).alias("bucket_id"),
            F.coalesce(F.col("src.cnt"), F.lit(0)).alias("src_cnt"),
            F.coalesce(F.col("tgt.cnt"), F.lit(0)).alias("tgt_cnt"),
            F.coalesce(F.col("src.p1"), F.lit(0)).alias("src_p1"),
            F.coalesce(F.col("tgt.p1"), F.lit(0)).alias("tgt_p1"),
            F.coalesce(F.col("src.p2"), F.lit(0)).alias("src_p2"),
            F.coalesce(F.col("tgt.p2"), F.lit(0)).alias("tgt_p2"),
            F.coalesce(F.col("src.p1_rh2"), F.lit(0)).alias("src_p1_rh2"),
            F.coalesce(F.col("tgt.p1_rh2"), F.lit(0)).alias("tgt_p1_rh2"),
            F.coalesce(F.col("src.p2_rh2"), F.lit(0)).alias("src_p2_rh2"),
            F.coalesce(F.col("tgt.p2_rh2"), F.lit(0)).alias("tgt_p2_rh2"),
        )
    ).cache()

    try:  # pylint: disable=too-many-try-statements  # try wraps the full detect+solve so finally can unpersist the cache once
        # All five signals (cnt + p1/p2/p1_rh2/p2_rh2) must agree before we declare MATCH.
        # Dropping rh2 from the OR-chain turns MD5 collisions into silent false MATCHes.
        mismatch_condition = (
            (F.col("src_cnt") != F.col("tgt_cnt"))
            | (F.col("src_p1") != F.col("tgt_p1"))
            | (F.col("src_p2") != F.col("tgt_p2"))
            | (F.col("src_p1_rh2") != F.col("tgt_p1_rh2"))
            | (F.col("src_p2_rh2") != F.col("tgt_p2_rh2"))
        )
        mismatched = joined.filter(mismatch_condition)

        # Single-agg pattern: one Spark job computes both ``total_sbs``
        # (denominator for the systemic-ratio guard) and ``mismatch_count``
        # (verdict + systemic-count guard). Calling joined.count() and
        # mismatched.count() separately would each trigger a full Stage-1
        # re-evaluation; folding into one agg halves Stage-1 wall-clock on every
        # run regardless of verdict.
        counts_row = joined.agg(
            F.count("*").alias("total_sbs"),
            F.sum(F.when(mismatch_condition, F.lit(1)).otherwise(F.lit(0)).cast("long")).alias("mismatch_count"),
        ).collect()[0]
        total_sbs = int(counts_row["total_sbs"] or 0)
        mismatch_count = int(counts_row["mismatch_count"] or 0)

        if mismatch_count == 0:
            logger.info("Fingerprint detection: MATCH — all sub-buckets identical")
            return DetectionResult(verdict="MATCH")

        mismatch_ratio = mismatch_count / max(total_sbs, 1)
        logger.info(
            f"Fingerprint detection: {mismatch_count}/{total_sbs} sub-buckets mismatched ({mismatch_ratio * 100:.1f}%)"
        )

        if mismatch_ratio > _SYSTEMIC_GUARD_THRESHOLD or mismatch_count > _MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE:
            logger.warning(
                f"Fingerprint: systemic mismatch ({mismatch_ratio * 100:.1f}% > "
                f"{_SYSTEMIC_GUARD_THRESHOLD * 100:.0f}% or {mismatch_count} > "
                f"{_MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE}) — falling through"
            )
            return DetectionResult(
                verdict="MISMATCH",
                total_mismatched_sbs=mismatch_count,
                systemic_mismatch=True,
            )

        # ``mismatched.collect()`` reads from the cached ``joined`` — no re-pull from
        # Redshift / Delta. Bounded by ``_MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE`` so the
        # driver-side list never exceeds 50K rows.
        mismatched_rows = mismatched.collect()

        solved_results: list[SolveResult] = []
        unsolved_sb_ids: list[int] = []

        for row in mismatched_rows:
            # Spark aggregates p1/p2/p1_rh2/p2_rh2 are DecimalType(38, 0) (see
            # spark_target._hash_agg_exprs — Decimal is required to avoid 64-bit
            # overflow when summing rh*rh). They surface here as decimal.Decimal,
            # which math.isqrt() in _solve_d2_extras rejects with
            # ``TypeError: 'decimal.Decimal' object cannot be interpreted as an integer``.
            # Cast to int up front so every downstream solver works on native ints.
            sb_id = int(row["sub_bucket_id"])
            d_cnt = int(row["src_cnt"]) - int(row["tgt_cnt"])
            d_p1 = int(row["src_p1"]) - int(row["tgt_p1"])
            d_p2 = int(row["src_p2"]) - int(row["tgt_p2"])
            d_p1_rh2 = int(row["src_p1_rh2"]) - int(row["tgt_p1_rh2"])
            d_p2_rh2 = int(row["src_p2_rh2"]) - int(row["tgt_p2_rh2"])

            result = _solve_sub_bucket(sb_id, d_cnt, d_p1, d_p2, d_p1_rh2, d_p2_rh2)
            if result is not None:
                solved_results.append(result)
            else:
                unsolved_sb_ids.append(sb_id)

        logger.info(
            f"Fingerprint solver: {len(solved_results)} solved, "
            f"{len(unsolved_sb_ids)} unsolved out of {len(mismatched_rows)} mismatched sub-buckets"
        )

        return DetectionResult(
            verdict="MISMATCH",
            solved_results=solved_results,
            unsolved_sb_ids=unsolved_sb_ids,
            total_mismatched_sbs=mismatch_count,
        )
    finally:
        # Always release the cache — every return path above lands here, including
        # the systemic-mismatch fallback and any exception bubbling up to the caller.
        joined.unpersist()


def _solve_sub_bucket(
    sb_id: int,
    d_cnt: int,
    d_p1: int,
    d_p2: int,
    d_p1_rh2: int,
    d_p2_rh2: int,
) -> SolveResult | None:
    """Solve d=1 / d=2 cases for one mismatched sub-bucket; return None when unsolvable."""
    abs_d = abs(d_cnt)
    if abs_d == 1:
        return solve_d1(sb_id, d_cnt, d_p1, d_p2, d_p1_rh2, d_p2_rh2)
    if abs_d == 0 and d_p1 != 0:
        return solve_d2_swap(sb_id, d_p1, d_p2, d_p1_rh2, d_p2_rh2)
    if abs_d == 2:
        return _solve_d2_extras(sb_id, d_cnt, d_p1, d_p2, d_p1_rh2, d_p2_rh2)
    return None


def solve_d1(sb_id: int, d_cnt: int, d_p1: int, d_p2: int, d_p1_rh2: int, d_p2_rh2: int) -> SolveResult | None:
    """Solve d=1: one extra row on one side.

    culprit_rh = abs(d_p1); verified by d_p2 == rh^2 * sign(d_cnt) on both rh1 and
    rh2 channels. Range check rejects values outside the 32-bit MD5-extraction band.
    """
    rh1 = abs(d_p1)

    # Without the p2 verification, two rows that cancel in p1 (e.g. 3+7 == 1+9) would
    # be falsely "solved" with a wrong hash.
    if d_p2 != rh1 * rh1 * d_cnt:
        return None
    if rh1 < 0 or rh1 > _MAX_RH_VALUE:
        return None

    rh2 = abs(d_p1_rh2)
    if d_p2_rh2 != rh2 * rh2 * d_cnt:
        return None
    if rh2 < 0 or rh2 > _MAX_RH_VALUE:
        return None

    if d_cnt > 0:
        return SolveResult(sub_bucket_id=sb_id, source_hashes=[rh1], target_hashes=[])
    return SolveResult(sub_bucket_id=sb_id, source_hashes=[], target_hashes=[rh1])


def solve_d2_swap(
    sb_id: int,
    d_p1: int,
    d_p2: int,
    d_p1_rh2: int,
    d_p2_rh2: int,
) -> SolveResult | None:
    """Solve d=2 swap: same row count, one row's content changed.

    Source has h_old, target has h_new. d_p1 = h_old - h_new, and
    d_p2 = h_old^2 - h_new^2 = (h_old - h_new)(h_old + h_new). Solve for both
    and dual-slice-verify on rh2.
    """
    if d_p1 == 0:
        return None
    if d_p2 % d_p1 != 0:
        return None

    h_sum = d_p2 // d_p1
    # Parity guard: floor-division loses a bit otherwise and produces a wrong root.
    if (d_p1 + h_sum) % 2 != 0:
        return None
    h_old = (d_p1 + h_sum) // 2
    h_new = h_sum - h_old

    if h_old - h_new != d_p1 or h_old * h_old - h_new * h_new != d_p2:
        return None
    if not (0 <= h_old <= _MAX_RH_VALUE and 0 <= h_new <= _MAX_RH_VALUE):
        return None
    # Sign-product guard: real 1-for-1 swaps have non-negative roots on both sides.
    if h_old * h_new < 0:
        return None
    if not _cross_verify_d2_swap(d_p1_rh2, d_p2_rh2):
        return None

    return SolveResult(sub_bucket_id=sb_id, source_hashes=[h_old], target_hashes=[h_new])


def _solve_d2_extras(
    sb_id: int,
    d_cnt: int,
    d_p1: int,
    d_p2: int,
    d_p1_rh2: int,
    d_p2_rh2: int,
) -> SolveResult | None:
    """Solve d=2 extras: two extra rows on one side.

    Sign-adjusts the deltas (target-extras case has negative d_p1/d_p2), then solves
    the quadratic ``x^2 - sum_h*x + product = 0``.
    """
    sign = 1 if d_cnt > 0 else -1
    sum_h = d_p1 * sign
    sum_sq = d_p2 * sign

    if sum_sq < 0:
        return None

    product_2 = sum_h * sum_h - sum_sq
    if product_2 < 0 or product_2 % 2 != 0:
        return None
    product = product_2 // 2

    discriminant = sum_h * sum_h - 4 * product
    if discriminant < 0:
        return None

    sqrt_disc = _isqrt(discriminant)
    if sqrt_disc is None:
        return None
    if (sum_h + sqrt_disc) % 2 != 0:
        return None

    hash_a = (sum_h + sqrt_disc) // 2
    hash_b = sum_h - hash_a

    if hash_a + hash_b != sum_h or hash_a * hash_a + hash_b * hash_b != sum_sq:
        return None
    if not (0 <= hash_a <= _MAX_RH_VALUE and 0 <= hash_b <= _MAX_RH_VALUE):
        return None
    if not _cross_verify_d2_extras(d_cnt, d_p1_rh2, d_p2_rh2):
        return None

    # Repeated root means a single culprit hash appearing twice — emit it once so the
    # row-fetch phase doesn't issue the same predicate twice.
    hashes = [hash_a] if hash_a == hash_b else sorted([hash_a, hash_b])
    if d_cnt > 0:
        return SolveResult(sub_bucket_id=sb_id, source_hashes=hashes, target_hashes=[])
    return SolveResult(sub_bucket_id=sb_id, source_hashes=[], target_hashes=hashes)


def _cross_verify_d2_swap(d_p1_rh2: int, d_p2_rh2: int) -> bool:
    """Independently solve the d=2 swap on the rh2 channel and confirm valid roots."""
    if d_p1_rh2 == 0:
        return d_p2_rh2 == 0
    if d_p2_rh2 % d_p1_rh2 != 0:
        return False
    h_sum = d_p2_rh2 // d_p1_rh2
    if (d_p1_rh2 + h_sum) % 2 != 0:
        return False
    h_old = (d_p1_rh2 + h_sum) // 2
    h_new = h_sum - h_old
    if h_old - h_new != d_p1_rh2:
        return False
    return 0 <= h_old <= _MAX_RH_VALUE and 0 <= h_new <= _MAX_RH_VALUE


def _cross_verify_d2_extras(d_cnt: int, d_p1_rh2: int, d_p2_rh2: int) -> bool:
    """Independently solve the d=2 extras quadratic on the rh2 channel."""
    sign = 1 if d_cnt > 0 else -1
    sum_h = d_p1_rh2 * sign
    sum_sq = d_p2_rh2 * sign
    if sum_sq < 0:
        return False
    product_2 = sum_h * sum_h - sum_sq
    if product_2 < 0 or product_2 % 2 != 0:
        return False
    discriminant = sum_h * sum_h - 4 * (product_2 // 2)
    if discriminant < 0:
        return False
    sqrt_disc = _isqrt(discriminant)
    if sqrt_disc is None:
        return False
    if (sum_h + sqrt_disc) % 2 != 0:
        return False
    h_a = (sum_h + sqrt_disc) // 2
    h_b = sum_h - h_a
    return 0 <= h_a <= _MAX_RH_VALUE and 0 <= h_b <= _MAX_RH_VALUE


def _isqrt(value: int) -> int | None:
    """Return the integer square root if ``value`` is a perfect square, else None."""
    if value < 0:
        return None
    root = math.isqrt(value)
    return root if root * root == value else None
