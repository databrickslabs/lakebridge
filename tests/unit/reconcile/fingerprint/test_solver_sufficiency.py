"""Randomized sufficiency + safety guards for the d<=2 algebraic solver.

CI-permanent versions of the solver-sufficiency proof under
``experiments/redshift_fingerprint_validation/solver_sufficiency/`` (which cross-checks
the same properties three ways: closed-form occupancy, Monte-Carlo, and this real
solver). These tests pin three properties at the unit level so a future edit to the
solver or the systemic guard can't silently regress them:

  1. NO FALSE SOLVE (the sharpest correctness invariant): over many randomized bucket
     configurations - including >=3-ball buckets the d<=2 solver is NOT meant to handle
     - the solver never returns a SolveResult that fails to cover the true differing
     row-hashes. It either solves correctly or declines (``None`` -> whole sub-bucket is
     brute-force fetched). A false solve would silently under-report a mismatch, so a
     regression here is a data-correctness bug, not just an efficiency one.

  2. COVERAGE: every clean <=2-ball bucket (d=1 / d=2-swap / d=2-extras, distinct rh1)
     is solved with the exact culprit hashes - i.e. d<3 really is enough for the
     configurations the model says it should handle.

  3. STRUCTURAL FLOOR: the systemic guard bounds the worst-case load factor per shipped
     tier, which places a floor on the fraction of mismatch records the solver resolves.
     Pinned at >= 98.8% against the *actual* guard constants and tier table, so loosening
     the guard or retiering can't quietly drop coverage below the number given to
     reviewers.

Model recap: each differing row is a ball thrown into one of N sub-buckets by
``ABS(MOD(rh1, N))``; a bucket is solvable iff it holds <=2 balls. See the experiments
README for the full derivation.
"""

import math
import random
from collections import Counter

from databricks.labs.lakebridge.reconcile.fingerprint import engine
from databricks.labs.lakebridge.reconcile.fingerprint.constants import (
    SUB_BUCKET_COUNT,
    SUB_BUCKET_TIERS,
)

_RH_MAX = 0xFFFFFFFF  # 32-bit MD5 slice range == engine.MAX_RH_VALUE
_SEED = 20260703
_TRIALS = 10_000
_SOLVE_RATE_FLOOR = 0.988  # the >=98.8% figure quoted to reviewers


def _solve(src_r1, src_r2, tgt_r1, tgt_r2):
    """Compute the five signal deltas exactly as ``detect_and_solve`` does, then solve."""
    d_cnt = len(src_r1) - len(tgt_r1)
    d_p1 = sum(src_r1) - sum(tgt_r1)
    d_p2 = sum(h * h for h in src_r1) - sum(h * h for h in tgt_r1)
    d_p1_rh2 = sum(src_r2) - sum(tgt_r2)
    d_p2_rh2 = sum(h * h for h in src_r2) - sum(h * h for h in tgt_r2)
    return engine.solve_sub_bucket(0, d_cnt, d_p1, d_p2, d_p1_rh2, d_p2_rh2)


def _diff_hashes(src_r1, tgt_r1):
    """rh1 values whose count differs across sides - the rows a correct fetch must cover."""
    src_counts, tgt_counts = Counter(src_r1), Counter(tgt_r1)
    return {h for h in set(src_r1) | set(tgt_r1) if src_counts[h] != tgt_counts[h]}


def _recovered(res):
    return set(res.source_hashes) | set(res.target_hashes)


def _distinct(rng, count):
    seen: set[int] = set()
    while len(seen) < count:
        seen.add(rng.randint(0, _RH_MAX))
    return list(seen)


def _gen_clean_le2(rng):
    """A cleanly-solvable <=2-ball bucket: d=1, d=2-swap, d=2-extras, or repeated-root."""
    kind = rng.choice(["d1", "swap", "extras", "extras_repeat"])
    if kind == "d1":  # one extra row on a random side
        rh1, rh2 = rng.randint(0, _RH_MAX), rng.randint(0, _RH_MAX)
        return ([rh1], [rh2], [], []) if rng.random() < 0.5 else ([], [], [rh1], [rh2])
    if kind == "swap":  # (1,1): one row's content changed, rh1 distinct
        pair = _distinct(rng, 2)
        return [pair[0]], [rng.randint(0, _RH_MAX)], [pair[1]], [rng.randint(0, _RH_MAX)]
    if kind == "extras":  # two distinct extra rows on a random side
        pair = _distinct(rng, 2)
        rh2s = [rng.randint(0, _RH_MAX), rng.randint(0, _RH_MAX)]
        return (pair, rh2s, [], []) if rng.random() < 0.5 else ([], [], pair, rh2s)
    # repeated root: the SAME row appears twice on one side (rh1 and rh2 both equal)
    rh1, rh2 = rng.randint(0, _RH_MAX), rng.randint(0, _RH_MAX)
    return [rh1, rh1], [rh2, rh2], [], []


def _gen_ge3(rng):
    """A >=3-ball bucket: beyond the d<=2 solver, must be declined (never a false solve)."""
    count = rng.randint(3, 8)
    src_r1, src_r2, tgt_r1, tgt_r2 = [], [], [], []
    for _ in range(count):
        rh1, rh2 = rng.randint(0, _RH_MAX), rng.randint(0, _RH_MAX)
        if rng.random() < 0.5:
            src_r1.append(rh1)
            src_r2.append(rh2)
        else:
            tgt_r1.append(rh1)
            tgt_r2.append(rh2)
    return src_r1, src_r2, tgt_r1, tgt_r2


def test_clean_le2_configs_always_solve_with_exact_hashes():
    """COVERAGE: every clean <=2-ball bucket is solved and recovers the exact culprits.

    Proves d<3 is sufficient for the configurations the occupancy model says the solver
    should handle - the direct, positive half of the sufficiency claim.
    """
    rng = random.Random(_SEED)
    for _ in range(_TRIALS):
        src_r1, src_r2, tgt_r1, tgt_r2 = _gen_clean_le2(rng)
        res = _solve(src_r1, src_r2, tgt_r1, tgt_r2)
        assert res is not None, f"clean <=2 bucket failed to solve: {(src_r1, tgt_r1)}"
        assert _recovered(res) == _diff_hashes(
            src_r1, tgt_r1
        ), f"recovered hashes do not match truth for {(src_r1, tgt_r1)}: {res}"


def test_solver_never_false_solves_including_beyond_capacity():
    """NO FALSE SOLVE: across <=2 AND >=3 configs, any returned solution covers the truth.

    The dangerous failure mode is a SolveResult that omits a genuinely-differing hash: the
    surgical fetch would then miss that row and under-report the mismatch. This asserts the
    universal safety invariant - ``res is None`` (declined -> brute-force) OR the recovered
    hash set is a superset of every differing rh1. >=3-ball buckets are included precisely
    because they are outside the solver's design range and must be declined, not guessed.
    """
    rng = random.Random(_SEED + 1)
    generators = (_gen_clean_le2, _gen_ge3)
    for _ in range(_TRIALS):
        gen = rng.choice(generators)
        src_r1, src_r2, tgt_r1, tgt_r2 = gen(rng)
        res = _solve(src_r1, src_r2, tgt_r1, tgt_r2)
        if res is None:
            continue  # declined -> whole sub-bucket brute-force fetched, never a miss
        truth = _diff_hashes(src_r1, tgt_r1)
        assert _recovered(res) >= truth, (
            f"FALSE SOLVE: recovered {_recovered(res)} does not cover true diff {truth} "
            f"for src={src_r1} tgt={tgt_r1}"
        )


def test_cross_side_rh1_collision_declines_safely():
    """A source/target 32-bit rh1 collision (rh2 differs) is DECLINED, never mis-solved.

    This is the case that would be a false MATCH under a single-channel sketch: rh1 counts
    and power sums agree, so only the rh2 channel flags the bucket as mismatched. The rh1
    delta is degenerate (d_cnt=0, d_p1=0), so the solver declines and the sub-bucket is
    brute-force fetched - exactly the safe behaviour the second channel exists to enable.
    """
    r2_src, r2_tgt = 111, 222
    res = engine.solve_sub_bucket(
        0,
        d_cnt=0,
        d_p1=0,
        d_p2=0,
        d_p1_rh2=r2_src - r2_tgt,
        d_p2_rh2=r2_src * r2_src - r2_tgt * r2_tgt,
    )
    assert res is None


def _solve_rate_floor(sub_bucket_count: int) -> float:
    """Worst-case fraction of mismatch records the solver resolves on a tier of size N.

    The systemic guard bails to full recon once mismatched buckets exceed
    ``min(MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE, SYSTEMIC_GUARD_THRESHOLD * N)``. That caps
    the load factor lambda the solver can ever see, and the occupancy solve rate
    ``e^-lambda (1 + lambda)`` is monotonic decreasing in lambda, so the cap is the floor.
    """
    cap = min(engine.MAX_MISMATCHED_SUBBUCKETS_TO_SOLVE, engine.SYSTEMIC_GUARD_THRESHOLD * sub_bucket_count)
    lam_max = -math.log(1.0 - cap / sub_bucket_count)
    return math.exp(-lam_max) * (1.0 + lam_max)


def test_systemic_guard_enforces_solve_rate_floor_per_tier():
    """STRUCTURAL FLOOR: on every shipped tier the guard keeps the solve rate >= 98.8%.

    Pinned against the real ``SUB_BUCKET_TIERS`` and the real guard constants on
    ``engine``, so relaxing the guard or changing the tiers fails CI if it would drop the
    solver's coverage below the figure quoted to reviewers.
    """
    tier_counts = {sub for (_max_rows, sub, _bucket) in SUB_BUCKET_TIERS}
    tier_counts.add(SUB_BUCKET_COUNT)  # static fallback tier
    for sub_bucket_count in sorted(tier_counts):
        floor = _solve_rate_floor(sub_bucket_count)
        assert floor >= _SOLVE_RATE_FLOOR, (
            f"tier N={sub_bucket_count}: solve-rate floor {floor:.4%} dropped below "
            f"{_SOLVE_RATE_FLOOR:.1%} - the systemic guard or tier table was loosened"
        )

    # The smallest tier (bounded by the 15% ratio guard) is the binding worst case.
    smallest = min(tier_counts)
    assert _solve_rate_floor(smallest) == min(_solve_rate_floor(n) for n in tier_counts)
