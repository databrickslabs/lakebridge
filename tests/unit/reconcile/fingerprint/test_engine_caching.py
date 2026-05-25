"""Regression tests for the ``detect_and_solve`` Stage-1 caching contract.

The naive shape — ``joined.count()`` + ``mismatched.count()`` +
``mismatched.collect()`` — would fire three Spark jobs and re-evaluate the JDBC
+ Delta read each time. The current implementation fires two jobs (one agg +
one collect) reading from a cached frame.

These tests verify three invariants that must hold on every release:

1. The joined DataFrame is ``.cache()``-d before any action runs.
2. ``.unpersist()`` is invoked on every return path (MATCH, systemic-MISMATCH,
   solver-MISMATCH) — leaking a cached frame at 4M sub-buckets pins ~120 MB of
   driver memory until GC, which is a slow leak in long-lived clusters.
3. Counts are derived from a single ``.agg(...).collect()`` call — not two
   ``.count()`` calls — so we don't pay the Stage-1 wall-clock twice.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock, call

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint import engine
from databricks.labs.lakebridge.reconcile.fingerprint.engine import detect_and_solve


def _function_calls(func):
    """Collect ``Name.attr(...)`` call signatures in a function body, ignoring docstrings.

    Only captures calls whose receiver is a bare ``Name`` (e.g.
    ``joined.count()``). The contract is specifically about NOT calling
    ``count()`` on the named ``joined`` / ``mismatched`` locals. Chained
    attribute calls (``(...).select(...).cache()``) are tracked separately via
    substring inspection because they don't have a Name receiver.
    """
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            inner = node.func.value
            if isinstance(inner, ast.Name):
                calls.append(f"{inner.id}.{node.func.attr}")
    return calls


def _function_body_text(func) -> str:
    """Return the source body with the docstring stripped.

    Substring checks against this body cannot be tricked by mentions of forbidden
    patterns inside docstrings or comments-as-strings.
    """
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    func_node = tree.body[0]
    if (
        func_node.body
        and isinstance(func_node.body[0], ast.Expr)
        and isinstance(func_node.body[0].value, ast.Constant)
        and isinstance(func_node.body[0].value.value, str)
    ):
        # Drop the docstring node before unparsing.
        func_node.body = func_node.body[1:]
    return ast.unparse(func_node)


def _build_chain(*, total_sbs: int, mismatch_count: int):
    """Mock the full Spark chain reached from ``detect_and_solve``.

    Returns ``(source_agg_df, target_agg_df, joined_mock)`` so tests can both invoke
    the function and assert on the cached frame.
    """
    joined = MagicMock(name="joined_select")

    select_chain = MagicMock(name="select_chain")
    select_chain.cache.return_value = joined

    join_chain = MagicMock(name="join_chain")
    join_chain.select.return_value = select_chain

    src = MagicMock(name="source_agg_df")
    src_alias = MagicMock(name="source_alias")
    src_alias.join.return_value = join_chain
    src.alias.return_value = src_alias

    tgt = MagicMock(name="target_agg_df")
    tgt.alias.return_value = MagicMock(name="target_alias")

    counts_row = MagicMock(name="counts_row")
    counts_row.__getitem__.side_effect = lambda key: {
        "total_sbs": total_sbs,
        "mismatch_count": mismatch_count,
    }[key]

    agg_result = MagicMock(name="agg_result")
    agg_result.collect.return_value = [counts_row]

    joined.agg.return_value = agg_result
    joined.filter.return_value = MagicMock(name="mismatched", **{"collect.return_value": []})
    return src, tgt, joined


def test_match_path_caches_and_unpersists():
    src, tgt, joined = _build_chain(total_sbs=1024, mismatch_count=0)

    result = detect_and_solve(src, tgt)

    assert result.verdict == "MATCH"
    # Cache contract: select chain caches before agg/collect.
    src.alias.return_value.join.return_value.select.return_value.cache.assert_called_once_with()
    # Released on the MATCH return.
    joined.unpersist.assert_called_once_with()


def test_systemic_mismatch_path_unpersists():
    # Mismatch ratio > 0.15 -> systemic guard kicks in before the solver runs.
    src, tgt, joined = _build_chain(total_sbs=100, mismatch_count=20)

    result = detect_and_solve(src, tgt)

    assert result.verdict == "MISMATCH"
    assert result.systemic_mismatch is True
    joined.unpersist.assert_called_once_with()
    # Solver path must not have been entered: filter().collect() is never reached.
    joined.filter.return_value.collect.assert_not_called()


def test_solver_mismatch_path_unpersists():
    # Sub-systemic ratio (1/1024 ≈ 0.1%) -> solver path runs through ``.collect()``
    # on the filtered mock, which yields zero rows so the solver list is empty.
    src, tgt, joined = _build_chain(total_sbs=1024, mismatch_count=1)

    result = detect_and_solve(src, tgt)

    assert result.verdict == "MISMATCH"
    assert result.systemic_mismatch is False
    joined.filter.return_value.collect.assert_called_once_with()
    joined.unpersist.assert_called_once_with()


def test_solver_path_uses_cached_frame_only_once_for_counts():
    """Stage-1 must not regress to per-metric .count() calls.

    The single-agg pattern is what makes Stage-1 cheap on long-tail tables; if a
    future refactor reintroduces ``joined.count()`` or ``mismatched.count()`` we'd
    silently double the read cost. Assert via attribute-call inspection.
    """
    src, tgt, joined = _build_chain(total_sbs=1024, mismatch_count=0)

    detect_and_solve(src, tgt)

    # Exactly one agg invocation; zero standalone .count() calls on the cached frame
    # or its filter view.
    assert joined.agg.call_count == 1
    assert joined.count.call_count == 0, "joined.count() reintroduced — single-agg Stage-1 contract broken"
    assert (
        joined.filter.return_value.count.call_count == 0
    ), "mismatched.count() reintroduced — single-agg Stage-1 contract broken"


def test_unpersist_runs_even_when_solver_path_raises():
    """``finally`` block must release the cache on exception.

    Otherwise a corrupt agg row (e.g. NULL ``mismatch_count`` cast failure in some
    future Spark upgrade) would orphan the cached frame on the executors.
    """
    src, tgt, joined = _build_chain(total_sbs=1024, mismatch_count=1)
    joined.filter.return_value.collect.side_effect = RuntimeError("simulated executor failure")

    with pytest.raises(RuntimeError, match="simulated executor failure"):
        detect_and_solve(src, tgt)

    joined.unpersist.assert_called_once_with()


def test_source_inspection_documents_single_agg_pattern():
    """Belt-and-braces AST guard against future drift.

    The agg/cache pattern is the contract — if a future refactor removes
    ``.cache()`` or replaces the agg with two ``.count()`` calls the behavioural
    tests above might still pass under a sufficiently clever mock, but
    real-world Stage-1 cost would double silently. AST-walk the call sites so
    docstring mentions of ``joined.count`` (which describe what NOT to do)
    don't trigger false alarms.
    """
    body = _function_body_text(engine.detect_and_solve)
    calls = _function_calls(engine.detect_and_solve)

    assert ".cache()" in body, "joined frame must be cached before any Spark action"
    assert "joined.unpersist" in calls, "joined frame must be released on every return path"
    assert "joined.count" not in calls, (
        "single-agg Stage-1 contract violated: detect_and_solve calls joined.count() — "
        "fold into the existing joined.agg(...) instead"
    )
    assert "mismatched.count" not in calls, (
        "single-agg Stage-1 contract violated: detect_and_solve calls mismatched.count() — "
        "fold into the existing joined.agg(...) using F.when(condition, 1).otherwise(0)"
    )
    assert "joined.agg" in calls, "single-agg Stage-1 contract requires joined.agg(...)"


def test_counts_row_reads_total_and_mismatch_keys():
    """Agg projection must alias both fields the verdict logic reads.

    Renaming ``total_sbs`` or ``mismatch_count`` without updating the agg breaks
    silently — KeyError gets swallowed by the ``or 0`` fallback in some refactors.
    """
    src, tgt, joined = _build_chain(total_sbs=10, mismatch_count=0)
    detect_and_solve(src, tgt)

    counts_row = joined.agg.return_value.collect.return_value[0]
    assert call("total_sbs") in counts_row.__getitem__.call_args_list
    assert call("mismatch_count") in counts_row.__getitem__.call_args_list
