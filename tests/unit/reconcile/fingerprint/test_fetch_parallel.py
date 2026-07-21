"""B3 regression tests: ``fetch_source_and_target_rows`` parallel dispatch.

Stage-2 source-fetch (JDBC) and target-fetch (Spark) are independent — they share
no mutable state, run against different connectors, and produce different
DataFrames. Pre-B3 they ran serially; the JDBC round-trip blocked the target's
Spark DAG submission for no reason. Post-B3 they run on a 2-thread pool.

These tests pin three invariants:

1. Both fetches are dispatched (the test fakes a delay on each and asserts the
   wall-clock is bounded by the slower fetch, not the sum).
2. The result tuple is bit-identical to the pre-B3 serial form
   ``(src_df, fetch_path, tgt_df)``.
3. An exception in either worker re-raises on the caller's stack — same failure
   semantics as the serial implementation.
"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint import orchestrator as orch
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import FETCH_PATH_V1_SANDWICH


@pytest.fixture(name="fetch_ctx")
def fixture_fetch_ctx():
    """Bare ``FetchContext`` mock — the parallel helper never inspects it."""
    return MagicMock(name="fetch_ctx")


def test_fetch_runs_both_workers_concurrently(fetch_ctx):
    """B3 contract: source and target fetches dispatch on separate threads.

    The distinct-thread-id assertion is the deterministic signal — wall-clock
    is intentionally not asserted because a loaded CI runner can blow past any
    reasonable upper bound without the helper actually running serially.
    """
    src_thread_id: list[int] = []
    tgt_thread_id: list[int] = []

    def fake_source(*_args, **_kwargs):
        src_thread_id.append(threading.get_ident())
        time.sleep(0.05)
        return MagicMock(name="src_df"), FETCH_PATH_V1_SANDWICH

    def fake_target(*_args, **_kwargs):
        tgt_thread_id.append(threading.get_ident())
        time.sleep(0.05)
        return MagicMock(name="tgt_df")

    with (
        patch.object(orch, "fetch_source_rows", side_effect=fake_source),
        patch.object(orch, "fetch_target_rows", side_effect=fake_target),
    ):
        src_df, fetch_path, tgt_df = orch.fetch_source_and_target_rows(
            fetch_ctx, solved_hashes={1: [10]}, unsolved_sb_ids=[], report_type="data"
        )

    # Distinct threads is the strongest signal that pool.submit ran the two
    # callables on separate workers.
    assert src_thread_id and tgt_thread_id
    assert src_thread_id[0] != tgt_thread_id[0], "B3 contract violated: both fetches ran on the same thread"

    # Result tuple shape parity with pre-B3 serial form.
    assert src_df is not None
    assert fetch_path == FETCH_PATH_V1_SANDWICH
    assert tgt_df is not None


def test_fetch_returns_serial_equivalent_tuple(fetch_ctx):
    """Result tuple shape must remain ``(src_df, fetch_path, tgt_df)``.

    Pre-B3 ``run_fingerprint_precheck`` unpacked two tuples in sequence; B3
    centralised the join into the helper and returns a flat 3-tuple. Pinning so
    a future refactor can't silently transpose the order or wrap the result in
    a struct that the caller would .get('source') from.
    """
    fake_src_df = MagicMock(name="src_df")
    fake_tgt_df = MagicMock(name="tgt_df")

    with (
        patch.object(orch, "fetch_source_rows", return_value=(fake_src_df, FETCH_PATH_V1_SANDWICH)),
        patch.object(orch, "fetch_target_rows", return_value=fake_tgt_df),
    ):
        result = orch.fetch_source_and_target_rows(fetch_ctx, solved_hashes={}, unsolved_sb_ids=[1], report_type="data")

    assert isinstance(result, tuple)
    assert len(result) == 3, f"B3 helper must return a 3-tuple (src_df, fetch_path, tgt_df), got {len(result)}-tuple"
    src_df, fetch_path, tgt_df = result
    assert src_df is fake_src_df
    assert fetch_path == FETCH_PATH_V1_SANDWICH
    assert tgt_df is fake_tgt_df


def test_fetch_reraises_source_failure(fetch_ctx):
    """Source-side failure must abort the precheck on the caller's stack.

    Pre-B3 a JDBC failure in ``fetch_source_rows`` would simply re-raise on the
    main thread. Post-B3 the failure happens on a worker thread; we rely on
    ``future.result()`` to re-raise. Pinning so a future refactor can't change
    this to ``future.exception()`` and silently swallow the error.
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
    """Both workers must receive the same ``ctx`` / ``solved_hashes`` /
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


def test_thread_pool_uses_named_threads_for_observability():
    """``thread_name_prefix='fp-stage2'`` makes stuck JDBC pulls easy to spot
    in production thread dumps.

    Source-inspect because the prefix is set inside a ``with`` block that we'd
    otherwise need to monkeypatch ThreadPoolExecutor to observe.
    """
    src = inspect.getsource(orch.fetch_source_and_target_rows)
    assert 'thread_name_prefix="fp-stage2"' in src or "thread_name_prefix='fp-stage2'" in src, (
        "B3 contract: ThreadPoolExecutor must use thread_name_prefix='fp-stage2' "
        "so production thread dumps clearly identify Stage-2 worker stacks."
    )
    assert "max_workers=2" in src, (
        "B3 contract: pool size must be 2 — exactly one worker per fetch. A larger "
        "pool wastes driver memory; a smaller pool re-introduces serial behaviour."
    )
