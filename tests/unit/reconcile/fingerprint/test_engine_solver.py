"""Unit tests for fingerprint algebraic solver helpers."""

import inspect
from decimal import Decimal

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint import engine
from databricks.labs.lakebridge.reconcile.fingerprint.engine import solve_d1, solve_d2_swap


def test_solve_d1_uses_abs_of_delta():
    # d_cnt=-1, d_p1=-42 => rh=42, d_p2 == 42^2 * (-1) = -1764
    result_neg = solve_d1(1, -1, -42, -1764, -10, -100)
    assert result_neg is not None
    assert result_neg.target_hashes == [42]
    assert not result_neg.source_hashes

    # d_cnt=1, d_p1=99 => rh=99, d_p2 == 99^2 * 1 = 9801
    result_pos = solve_d1(2, 1, 99, 9801, 50, 2500)
    assert result_pos is not None
    assert result_pos.source_hashes == [99]


def test_solve_d1_rejects_wrong_p2():
    # rh=42 but d_p2 != 42^2 * 1 = 1764 — p2 verification rejects.
    assert solve_d1(1, 1, 42, 9999, 10, 100) is None


def test_solve_d2_swap_basic():
    # h_old=5, h_new=3 -> d_p1=2, d_p2=25-9=16
    result = solve_d2_swap(1, 2, 16, 4, 40)
    assert result is not None
    assert result.source_hashes == [5]
    assert result.target_hashes == [3]


def test_detect_and_solve_mismatch_filter_covers_rh2_channel():
    """All five signals (cnt + p1 + p2 + p1_rh2 + p2_rh2) must be in the mismatch OR.

    Source-inspection because ``detect_and_solve`` returns a DetectionResult that doesn't
    surface the filter expression. Dropping rh2 from the OR turns MD5 collisions into
    silent false MATCH verdicts.
    """
    src = inspect.getsource(engine.detect_and_solve)
    assert 'F.col("src_p1_rh2") != F.col("tgt_p1_rh2")' in src
    assert 'F.col("src_p2_rh2") != F.col("tgt_p2_rh2")' in src


def test_solve_d2_extras_handles_target_side_negative_d_cnt():
    """``d_cnt < 0`` (target has the extras) — sign-adjustment is required.

    Without it ``sum_sq < 0`` short-circuits and every target-side d=2 case silently
    fails to solve.
    """
    h_a, h_b = 5, 3
    d_cnt = -2
    d_p1 = -(h_a + h_b)
    d_p2 = -(h_a * h_a + h_b * h_b)
    rh2_a, rh2_b = 7, 11
    d_p1_rh2 = -(rh2_a + rh2_b)
    d_p2_rh2 = -(rh2_a * rh2_a + rh2_b * rh2_b)

    result = engine.solve_sub_bucket(
        sb_id=99,
        d_cnt=d_cnt,
        d_p1=d_p1,
        d_p2=d_p2,
        d_p1_rh2=d_p1_rh2,
        d_p2_rh2=d_p2_rh2,
    )

    assert result is not None
    assert not result.source_hashes
    assert sorted(result.target_hashes) == [3, 5]


def test_solve_d2_swap_rejects_weak_rh2_cross_verify():
    """rh2 cross-verification must independently solve the quadratic, not just check divisibility.

    Construction: rh1 channel is a valid swap (d_p1=2, d_p2=16). rh2 deltas pass the
    divisibility check (d_p2_rh2 % d_p1_rh2 == 0) but the recovered roots fail parity.
    """
    assert solve_d2_swap(sb_id=42, d_p1=2, d_p2=16, d_p1_rh2=2, d_p2_rh2=10) is None


def test_solve_d2_swap_rejects_odd_parity_delta():
    """Parity guard: ``(d_p1 + h_sum)`` odd would lose a bit in floor-division."""
    # d_p1=2, d_p2=6 -> h_sum=3, (2+3)=5 odd -> reject
    assert solve_d2_swap(42, d_p1=2, d_p2=6, d_p1_rh2=0, d_p2_rh2=0) is None


def test_solve_d2_swap_rejects_out_of_range_root():
    """Roots outside [0, 0xFFFFFFFF] cannot come from a 32-bit MD5 extraction."""
    h_old = 0x1_0000_0000
    h_new = 0
    d_p1 = h_old - h_new
    d_p2 = h_old * h_old - h_new * h_new
    assert solve_d2_swap(7, d_p1, d_p2, d_p1_rh2=0, d_p2_rh2=0) is None


def test_solve_d2_swap_rejects_negative_root_product():
    """``h_old * h_new < 0`` proves the candidate pair is non-physical (hashes are unsigned)."""
    # h_old=10, h_new=-2 -> d_p1=12, d_p2=96
    assert solve_d2_swap(11, d_p1=12, d_p2=96, d_p1_rh2=0, d_p2_rh2=0) is None


def test_solve_d2_extras_dedupes_repeated_root():
    """Repeated quadratic root means a single culprit hash that appears twice; emit once."""
    # h1 = h2 = 4, d_cnt=2 -> d_p1=8, d_p2=32
    result = solve_d1.__globals__["_solve_d2_extras"](
        sb_id=5,
        d_cnt=2,
        d_p1=8,
        d_p2=32,
        d_p1_rh2=8,
        d_p2_rh2=32,
    )
    assert result is not None
    assert result.source_hashes == [4]
    assert not result.target_hashes


def test_solve_d2_extras_rejects_decimal_inputs():
    """``math.isqrt`` only accepts native ``int`` — passing decimal.Decimal must raise.

    Regression guard. The Spark Stage-1 aggregates ``p1/p2/p1_rh2/p2_rh2`` are
    ``DecimalType(38, 0)`` (overflow-safe for sums of ``rh*rh``), so ``.collect()``
    surfaces them as ``decimal.Decimal``. If anything in ``detect_and_solve`` ever
    drops the explicit ``int(row[...])`` cast, MISMATCH scenarios that route into
    the d=2-extras solver (e.g. high-density mutations like
    ``D_1pct_mismatch_rate``) silently break with
    ``TypeError: 'decimal.Decimal' object cannot be interpreted as an integer``.
    This test pins the failure mode so the cast can't quietly disappear again.
    """
    with pytest.raises(TypeError, match="decimal.Decimal"):
        solve_d1.__globals__["_solve_d2_extras"](
            sb_id=5,
            d_cnt=2,
            d_p1=Decimal(8),
            d_p2=Decimal(32),
            d_p1_rh2=Decimal(8),
            d_p2_rh2=Decimal(32),
        )


def test_detect_and_solve_casts_row_values_to_int():
    """``detect_and_solve``'s row loop must coerce Spark Row values to native ``int``.

    Source-inspection because the actual loop runs against a Spark DataFrame that
    can't be cheaply faked here. The aggregates surface as ``decimal.Decimal``
    (see ``test_solve_d2_extras_rejects_decimal_inputs``); without explicit
    ``int(...)`` casts the ``_solve_d2_extras`` path raises ``TypeError`` whenever
    ``abs(d_cnt) >= 2`` (multiple mutations colliding in one sub-bucket — the
    failure pattern observed on the post-rebase Track 1 matrix for
    ``A_tgt_del_1000_batch`` and ``D_1pct_mismatch_rate``).
    """
    src = inspect.getsource(engine.detect_and_solve)
    for cast_expr in (
        'int(row["sub_bucket_id"])',
        'int(row["src_cnt"])',
        'int(row["tgt_cnt"])',
        'int(row["src_p1"])',
        'int(row["tgt_p1"])',
        'int(row["src_p2"])',
        'int(row["tgt_p2"])',
        'int(row["src_p1_rh2"])',
        'int(row["tgt_p1_rh2"])',
        'int(row["src_p2_rh2"])',
        'int(row["tgt_p2_rh2"])',
    ):
        assert cast_expr in src, f"detect_and_solve row loop is missing {cast_expr}"
