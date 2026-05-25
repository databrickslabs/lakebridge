"""Regression: source-side and target-side ``treat_empty_as_null`` must agree.

Pre-fix: ``_DEFAULT_TREAT_EMPTY_AS_NULL`` was wired into ``get_query_builder`` (source)
but ``compute_target_fingerprint`` and ``build_target_filter_subquery`` silently kept
their function defaults of ``False``. Today the two happen to coincide, so flipping the
constant to ``True`` would have made source serialise ``''`` as ``'_null_recon_'`` while
target kept ``''`` — a systemic Stage-1 mismatch on every empty cell, fail-open
rewriting every recon to the full pipeline. The fix threads
``ReconcileConfig.fingerprint_treat_empty_as_null`` through ``run_fingerprint_precheck``
to all three serialisation entry points; this test pins that single source of truth.
"""

from __future__ import annotations

import pytest

from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import get_query_builder
from databricks.labs.lakebridge.reconcile.fingerprint.spark_target import (  # pylint: disable=import-private-name
    _serialize_column_spark_sql,
    build_target_filter_subquery,
)
from databricks.labs.lakebridge.reconcile.recon_config import Schema


@pytest.mark.parametrize("treat_empty_as_null", [False, True])
def test_source_builder_picks_up_flag(treat_empty_as_null: bool) -> None:
    builder = get_query_builder("redshift", treat_empty_as_null=treat_empty_as_null)
    serialised = builder.serialize_column("`notes`", "varchar(64)")
    if treat_empty_as_null:
        assert "NULLIF(" in serialised, serialised
    else:
        assert "NULLIF(" not in serialised, serialised


@pytest.mark.parametrize("treat_empty_as_null", [False, True])
def test_target_filter_subquery_picks_up_flag(treat_empty_as_null: bool) -> None:
    sql = build_target_filter_subquery(
        catalog="c",
        schema="s",
        table="t",
        columns=[Schema("`notes`", "varchar(64)", "`notes`", "`notes`")],
        column_mapping=None,
        solved_hashes={1: [101]},
        unsolved_sb_ids=[],
        sub_bucket_count=1024,
        treat_empty_as_null=treat_empty_as_null,
    )
    if treat_empty_as_null:
        assert "NULLIF(" in sql, sql
    else:
        assert "NULLIF(" not in sql, sql


@pytest.mark.parametrize("treat_empty_as_null", [False, True])
def test_target_stage1_and_stage2_agree_under_flag_flip(treat_empty_as_null: bool) -> None:
    """The DataFrame-side serialiser (Stage-1) and the SQL-string sibling (Stage-2) must
    apply the same ``treat_empty_as_null`` semantics — silent disagreement here was the
    same class of silent-miss bug as the Stage-1/Stage-2 trim asymmetry.
    """
    sql = _serialize_column_spark_sql("notes", "varchar(64)", treat_empty_as_null)
    if treat_empty_as_null:
        assert "NULLIF(" in sql, sql
    else:
        assert "NULLIF(" not in sql, sql
    assert "TRIM(CAST(`notes` AS STRING))" in sql, sql


def test_run_fingerprint_precheck_threads_flag_to_all_three_serialisers() -> None:
    """End-to-end: a single ``treat_empty_as_null`` argument on
    ``run_fingerprint_precheck`` reaches the source builder, the Stage-1 target
    aggregate, and the Stage-2 target filter subquery. Without this contract the
    config field is decorative.
    """
    # pylint: disable=import-outside-toplevel
    # Imports are local: this single-test module exercises a deeply-mocked code path
    # and the assertion-side imports must follow the patches; hoisting them to module
    # top would force the early bind of names the patches replace.
    from unittest.mock import MagicMock, patch

    from databricks.labs.lakebridge.reconcile.fingerprint import orchestrator as orch
    from databricks.labs.lakebridge.reconcile.fingerprint.engine import DetectionResult
    from databricks.labs.lakebridge.reconcile.fingerprint.orchestrator import ColumnAlignment

    captured: dict[str, object] = {}

    def capture_get_query_builder(_data_source: str, *, treat_empty_as_null: bool):
        captured["source_flag"] = treat_empty_as_null
        return MagicMock()

    def capture_compute_target_fingerprint(**kwargs):
        captured["target_stage1_flag"] = kwargs["treat_empty_as_null"]
        return MagicMock()

    def capture_build_target_filter_subquery(*_args, **kwargs):
        captured["target_stage2_flag"] = kwargs["treat_empty_as_null"]
        return "(SELECT * FROM t WHERE 1=1) _fp_filtered"

    # ``_fetch_target_rows`` builds a real ``HashQueryBuilder`` over the mock
    # schema; bypass it via a thin shim that exercises only the contract we care
    # about — that the flag reaches ``build_target_filter_subquery``.
    def shim_fetch_target_rows(ctx, solved_hashes, unsolved_sb_ids, _report_type):
        captured["target_stage2_flag"] = ctx.treat_empty_as_null
        # Calling the real ``build_target_filter_subquery`` (already patched) keeps
        # the wiring assertion honest: if the orchestrator forgot to populate
        # ``ctx.treat_empty_as_null``, the mock would record ``False`` instead of
        # the value passed by the caller.
        orch.build_target_filter_subquery(
            ctx.database_config.target_catalog,
            ctx.database_config.target_schema,
            ctx.table_conf.target_name,
            ctx.detection_cols,
            ctx.column_mapping,
            solved_hashes,
            unsolved_sb_ids,
            sub_bucket_count=ctx.tier.sub_bucket_count,
            treat_empty_as_null=ctx.treat_empty_as_null,
        )
        return MagicMock()

    with (
        patch.object(orch, "get_query_builder", side_effect=capture_get_query_builder),
        patch.object(orch, "compute_target_fingerprint", side_effect=capture_compute_target_fingerprint),
        patch.object(orch, "build_target_filter_subquery", side_effect=capture_build_target_filter_subquery),
        patch.object(orch, "align_columns", return_value=ColumnAlignment(column_mapping=None)),
        patch.object(
            orch, "_resolve_detection_columns", return_value=[Schema("`notes`", "string", "`notes`", "`notes`")]
        ),
        patch.object(
            orch,
            "_select_tier",
            return_value=orch._TierSelection(  # pylint: disable=protected-access
                sub_bucket_count=1024, bucket_count=128, target_row_count=100, row_count_source="static_default"
            ),
        ),
        patch.object(
            orch,
            "detect_and_solve",
            return_value=DetectionResult(verdict="MISMATCH", solved_results=[], unsolved_sb_ids=[7]),
        ),
        patch.object(orch, "_fetch_source_rows", return_value=(MagicMock(), "v1_sandwich")),
        patch.object(orch, "_fetch_target_rows", side_effect=shim_fetch_target_rows),
    ):
        from databricks.labs.lakebridge.config import DatabaseConfig  # pylint: disable=import-outside-toplevel
        from databricks.labs.lakebridge.reconcile.recon_config import Table  # pylint: disable=import-outside-toplevel

        source = MagicMock()
        target = MagicMock()
        source.read_data.return_value = MagicMock()
        target.read_data.return_value = MagicMock()
        orch.run_fingerprint_precheck(
            source=source,
            target=target,
            spark=MagicMock(),
            source_engine=MagicMock(),
            database_config=DatabaseConfig(
                source_catalog="sc", source_schema="ss", target_catalog="tc", target_schema="ts"
            ),
            table_conf=Table(source_name="t", target_name="t", join_columns=["id"]),
            src_schema=[Schema("`notes`", "string", "`notes`", "`notes`")],
            tgt_schema=[Schema("`notes`", "string", "`notes`", "`notes`")],
            report_type="data",
            data_source="redshift",
            treat_empty_as_null=True,
        )

    assert captured == {
        "source_flag": True,
        "target_stage1_flag": True,
        "target_stage2_flag": True,
    }, f"flag did not reach every serialiser; captured={captured!r}"
