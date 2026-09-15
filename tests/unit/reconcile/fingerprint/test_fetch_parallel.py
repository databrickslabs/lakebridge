"""Regression tests: ``fetch_source_and_target_rows`` (serial Stage-2 dispatch).

Stage-2 source-fetch (JDBC) and target-fetch (Spark) both return LAZY DataFrames, so there is
no driver-side round trip to overlap; the helper runs them serially. These tests pin the
observable contract that a refactor must preserve:

1. The result tuple is ``(src_df, fetch_path, tgt_df)``.
2. An exception in either fetch re-raises on the caller's stack.
3. Both fetches receive the caller's arguments unmodified.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint import orchestrator as orch
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import FETCH_PATH_V1_SANDWICH


@pytest.fixture(name="fetch_ctx")
def fixture_fetch_ctx():
    """Bare ``FetchContext`` mock — the helper never inspects it."""
    return MagicMock(name="fetch_ctx")


def test_fetch_returns_serial_equivalent_tuple(fetch_ctx):
    """Result tuple shape must remain ``(src_df, fetch_path, tgt_df)``.

    The helper centralises the two fetches and returns a flat 3-tuple. Pinning so a future
    refactor can't silently transpose the order or wrap the result in a struct that the caller
    would .get('source') from.
    """
    fake_src_df = MagicMock(name="src_df")
    fake_tgt_df = MagicMock(name="tgt_df")

    with (
        patch.object(orch, "fetch_source_rows", return_value=(fake_src_df, FETCH_PATH_V1_SANDWICH)),
        patch.object(orch, "fetch_target_rows", return_value=fake_tgt_df),
    ):
        result = orch.fetch_source_and_target_rows(fetch_ctx, solved_hashes={}, unsolved_sb_ids=[1], report_type="data")

    assert isinstance(result, tuple)
    assert len(result) == 3, f"helper must return a 3-tuple (src_df, fetch_path, tgt_df), got {len(result)}-tuple"
    src_df, fetch_path, tgt_df = result
    assert src_df is fake_src_df
    assert fetch_path == FETCH_PATH_V1_SANDWICH
    assert tgt_df is fake_tgt_df


def test_fetch_reraises_source_failure(fetch_ctx):
    """Source-side failure must abort the precheck on the caller's stack.

    A JDBC failure in ``fetch_source_rows`` re-raises directly (serial dispatch). Pinning so a
    future refactor can't reintroduce a mechanism that swallows the error.
    """
    boom = RuntimeError("simulated JDBC connection drop")

    with (
        patch.object(orch, "fetch_source_rows", side_effect=boom),
        patch.object(orch, "fetch_target_rows", return_value=MagicMock(name="tgt_df")),
    ):
        with pytest.raises(RuntimeError, match="simulated JDBC connection drop"):
            orch.fetch_source_and_target_rows(fetch_ctx, solved_hashes={}, unsolved_sb_ids=[1], report_type="data")


def test_fetch_reraises_target_failure(fetch_ctx):
    """Target-side failure must also re-raise on the caller's stack."""
    boom = RuntimeError("simulated Delta read failure")

    with (
        patch.object(orch, "fetch_source_rows", return_value=(MagicMock(name="src_df"), FETCH_PATH_V1_SANDWICH)),
        patch.object(orch, "fetch_target_rows", side_effect=boom),
    ):
        with pytest.raises(RuntimeError, match="simulated Delta read failure"):
            orch.fetch_source_and_target_rows(fetch_ctx, solved_hashes={}, unsolved_sb_ids=[1], report_type="data")


def test_fetch_passes_arguments_through_unmodified(fetch_ctx):
    """Both fetches must receive the same ``ctx`` / ``solved_hashes`` /
    ``unsolved_sb_ids`` / ``report_type`` the caller passed.

    Pinning so a future refactor that, e.g., copies ``solved_hashes`` for one
    side but not the other can't silently produce inconsistent Stage-2 filters.
    """
    solved = {5: [101, 102], 9: [203]}
    unsolved = [7, 13]

    with (
        patch.object(orch, "fetch_source_rows", return_value=(MagicMock(), FETCH_PATH_V1_SANDWICH)) as src_spy,
        patch.object(orch, "fetch_target_rows", return_value=MagicMock()) as tgt_spy,
    ):
        orch.fetch_source_and_target_rows(fetch_ctx, solved, unsolved, "data")

    src_spy.assert_called_once_with(fetch_ctx, solved, unsolved, "data")
    tgt_spy.assert_called_once_with(fetch_ctx, solved, unsolved, "data")
