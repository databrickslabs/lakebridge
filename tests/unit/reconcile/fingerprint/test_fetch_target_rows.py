"""Unit tests for ``orchestrator._fetch_target_rows`` Stage-2 target-fetch behaviour.

Mirror of ``test_fetch_source_rows.py`` for the target-side helper. Pins:

1. The helper composes a single SQL statement by injecting
   ``build_target_filter_subquery`` (from ``spark_target``) into the target-side
   ``HashQueryBuilder`` query's ``:tbl`` placeholder — same "sandwich" shape as
   the source path::

       SELECT LOWER(SHA2(<concat>,256)) AS hash_value_recon, <join_cols>
       FROM (SELECT * FROM <full_table> WHERE <md5/sb filter>) _fp_filtered;

2. The defensive ``replace("%(tbl)s", ...)`` on the target side (orchestrator
   line ~480) is the same Bug R class guard as on the source side. The
   target ``HashQueryBuilder`` runs against the Databricks/Spark dialect
   today which keeps ``:tbl`` literal — but a future sqlglot upgrade or
   dialect bump that emits ``FROM %(tbl)s`` must not silently regress
   Stage-2 target-fetch into a full Delta scan. Both placeholder forms must
   be fully resolved before ``read_data``.

3. Standard ``read_data`` keyword set with ``options=None`` — the target
   side never forwards ``jdbc_reader_options`` (which only apply to the
   source JDBC). Pinning so a refactor cannot silently start passing them
   on Delta reads.

These are pure-string tests — no SparkSession or JDBC. We mock the target
connector and inspect the SQL that reaches ``read_data``. See Bug R in
``docs/REDSHIFT_CONNECTOR_BUG_FIXES.md`` and NEW-1 in
``docs/FINGERPRINT_INTEGRATION_REVIEW.md`` for why this surface needs
parity coverage with the source side.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.unit.reconcile.fingerprint._fixtures import assert_project_all_columns_kwargs, make_fetch_ctx

# Stand-in for the rendered target-side ``HashQueryBuilder.build_query("data")``
# output. We pin a deterministic string so tests assert the substitution
# ``_fetch_target_rows`` performs without depending on the live builder.
#
# Today the target-side ``HashQueryBuilder`` runs against the Databricks/Spark
# dialect and keeps ``:tbl`` literally. Tomorrow's sqlglot bump could swap to
# the PostgreSQL pyformat ``%(tbl)s`` rendering — same class of regression
# that bit Bug R on the source side. We parametrise over both forms so the
# defensive ``replace("%(tbl)s", ...)`` at orchestrator line ~480 is
# exercised.
_FAKE_TGT_HASH_QUERY_NAMED_FORM = (
    'SELECT LOWER(SHA2(COALESCE(TRIM(CAST(`order_amount` AS STRING)), '
    "'_null_recon_') || COALESCE(TRIM(CAST(`order_id` AS STRING)), "
    "'_null_recon_'), 256)) AS hash_value_recon, `order_id` AS `order_id` FROM :tbl"
)
_FAKE_TGT_HASH_QUERY_PG_FORM = _FAKE_TGT_HASH_QUERY_NAMED_FORM.replace(":tbl", "%(tbl)s")
# Default kept as the Spark-shaped form because that is the live rendering
# today. Tests that exercise the Bug-R-class regression form override locally.
_FAKE_TGT_HASH_QUERY = _FAKE_TGT_HASH_QUERY_NAMED_FORM

# Deterministic stand-in for ``build_target_filter_subquery``. Mirrors the real
# helper's contract: parenthesised subquery with the trailing ``_fp_filtered``
# alias so it can substitute into ``FROM :tbl`` directly.
_FAKE_TARGET_FILTER_SUBQUERY = (
    "(SELECT * FROM test_catalog.perf_test.orders "
    "WHERE ABS(MOD(CAST(CONV(SUBSTR(MD5(<concat>), 1, 8), 16, 10) AS BIGINT), 2097152)) "
    "IN (1, 2, 3)) _fp_filtered"
)


@pytest.fixture(name="target_mock")
def fixture_target_mock():
    """Plain ``DataSource`` mock — Stage-2 target-fetch always routes through ``read_data``."""
    target = MagicMock()
    target.read_data.return_value = MagicMock()  # DataFrame stand-in
    return target


def _make_fetch_ctx(target):
    """``_FetchContext`` wired to ``target``; source-side is a bare MagicMock (never touched here)."""
    return make_fetch_ctx(target=target)


def _patched_fetch_target_rows(*args, hash_query: str = _FAKE_TGT_HASH_QUERY, **kwargs):
    """Run ``_fetch_target_rows`` with ``HashQueryBuilder`` and
    ``build_target_filter_subquery`` stubbed to fixed strings.

    The real builder needs a fully-configured ``Schema`` / dialect / data-source
    pair plus sqlglot rendering; that is exhaustively tested elsewhere. Here
    we only care about the ``:tbl`` / ``%(tbl)s`` rewrite and how the filter
    subquery composes with the hash query.
    """
    from databricks.labs.lakebridge.reconcile.fingerprint import (  # pylint: disable=import-outside-toplevel
        orchestrator as orch,
    )

    fake_hash_builder = MagicMock()
    fake_hash_builder.build_query.return_value = hash_query
    with (
        patch.object(orch, "HashQueryBuilder", return_value=fake_hash_builder),
        patch.object(orch, "build_target_filter_subquery", return_value=_FAKE_TARGET_FILTER_SUBQUERY),
    ):
        return orch._fetch_target_rows(*args, **kwargs)  # pylint: disable=protected-access


def test_target_fetch_emits_single_statement_sandwich(target_mock):
    """Spark-dialect ``:tbl`` in the target hash query is replaced by the
    parenthesised filter subquery, producing one SELECT that filters
    (inside the parens) and projects SHA-256 (outside). Same sandwich shape
    as the source side.
    """
    ctx = _make_fetch_ctx(target_mock)

    df = _patched_fetch_target_rows(
        ctx,
        solved_hashes={1: [10, 20]},
        unsolved_sb_ids=[],
        report_type="data",
    )

    assert df is target_mock.read_data.return_value
    target_mock.read_data.assert_called_once()

    kwargs = target_mock.read_data.call_args.kwargs
    query = kwargs["query"]

    # Single-statement shape: starts with the projection, no WITH / CTE.
    assert query.lstrip().upper().startswith("SELECT "), query
    assert "WITH " not in query.upper().split("FROM", 1)[0], "no CTE prefix expected on sandwich path"
    # The sandwich: filter subquery (parenthesised + aliased) substituted into ``:tbl``.
    assert "_fp_filtered" in query, query
    assert _FAKE_TARGET_FILTER_SUBQUERY in query, query
    # Hash projection still runs (LOWER(SHA2(...,256))) on the target side.
    assert "SHA2" in query.upper()
    assert "hash_value_recon" in query
    # Both placeholder forms fully resolved. ``:tbl`` is the live Spark
    # rendering; ``%(tbl)s`` is the Bug-R-class guard for a future dialect
    # bump.
    assert ":tbl" not in query
    assert "%(tbl)s" not in query


def test_target_fetch_uses_standard_read_data_signature(target_mock):
    """Stage-2 target-fetch must use the standard ``read_data`` keyword set
    with ``options=None`` — ``jdbc_reader_options`` apply to the JDBC source
    only, never to the Delta target. Pinning so a refactor cannot silently
    start passing JDBC options on Delta reads.
    """
    ctx = _make_fetch_ctx(target_mock)

    _patched_fetch_target_rows(
        ctx,
        solved_hashes={1: [10]},
        unsolved_sb_ids=[],
        report_type="data",
    )

    kwargs = target_mock.read_data.call_args.kwargs
    # Standard ``read_data`` keyword set — no extra knobs.
    assert set(kwargs) == {"catalog", "schema", "table", "query", "options"}
    assert kwargs["catalog"] == "test_catalog"
    assert kwargs["schema"] == "perf_test"
    assert kwargs["table"] == "orders"
    # ``options`` MUST be ``None`` on the target side — JDBC reader options
    # are source-only.
    assert kwargs["options"] is None


def test_target_fetch_invokes_filter_subquery_with_tier_and_solver_outputs(target_mock):
    """The orchestrator must pass the adaptive tier's ``sub_bucket_count`` (so
    the target Stage-2 modulus matches Stage-1 detection) and the solver's
    ``solved_hashes`` / ``unsolved_sb_ids`` outputs verbatim to
    ``_build_target_filter_subquery``. Pinning this guards against a refactor
    accidentally swapping in the static ``constants.SUB_BUCKET_COUNT`` and
    breaking sub-bucket alignment between detection and target fetch
    (silent MATCH-not-MATCH false positives).
    """
    from databricks.labs.lakebridge.reconcile.fingerprint import (  # pylint: disable=import-outside-toplevel
        orchestrator as orch,
    )

    ctx = _make_fetch_ctx(target_mock)
    fake_hash_builder = MagicMock()
    fake_hash_builder.build_query.return_value = _FAKE_TGT_HASH_QUERY
    with (
        patch.object(orch, "HashQueryBuilder", return_value=fake_hash_builder),
        patch.object(orch, "build_target_filter_subquery", return_value=_FAKE_TARGET_FILTER_SUBQUERY) as filter_builder,
    ):
        orch._fetch_target_rows(  # pylint: disable=protected-access
            ctx,
            solved_hashes={5: [101, 102], 9: [203]},
            unsolved_sb_ids=[7, 13],
            report_type="data",
        )

    filter_builder.assert_called_once()
    args, call_kwargs = filter_builder.call_args
    # ``build_target_filter_subquery`` positional signature:
    # (catalog, schema, table, columns, column_mapping, solved_hashes, unsolved_sb_ids)
    # plus keyword-only sub_bucket_count.
    assert call_kwargs["sub_bucket_count"] == 2_097_152, (
        "Stage-2 target fetch must use the same adaptive sub_bucket_count as "
        "Stage-1 detection — otherwise the target WHERE predicate's sub-bucket "
        "modulus won't match the IDs the solver produced and the filter "
        "returns no rows (silent MATCH false positive)."
    )
    assert args[5] == {5: [101, 102], 9: [203]}, "solved_hashes must reach the helper unchanged"
    assert args[6] == [7, 13], "unsolved_sb_ids must reach the helper unchanged"


# --- Bug R parity coverage (NEW-1, 2026-05-09) -------------------------------
#
# The defensive ``replace("%(tbl)s", tgt_filter_subquery)`` at orchestrator
# line ~480 is the same class of guard that fixed Bug R on the source side.
# Today the target ``HashQueryBuilder`` runs against the Databricks/Spark
# dialect and keeps ``:tbl`` literal; tomorrow's sqlglot bump could swap to
# the PostgreSQL pyformat ``%(tbl)s`` rendering. Without this regression
# coverage the defensive substitution would silently rot — a future sqlglot
# upgrade that switches the Spark dialect rendering to ``%(tbl)s`` could
# regress Stage-2 target-fetch into a full Delta scan, producing the same
# phantom-counts symptom Bug R produced on the source side.


@pytest.mark.parametrize(
    "hash_query_template",
    [_FAKE_TGT_HASH_QUERY_NAMED_FORM, _FAKE_TGT_HASH_QUERY_PG_FORM],
    ids=["spark-named-:tbl", "pyformat-%(tbl)s"],
)
def test_target_fetch_resolves_both_placeholder_forms_for_dialect_parity(target_mock, hash_query_template):
    """Both ``:tbl`` (Spark/named, today's live form) and ``%(tbl)s``
    (Postgres/pyformat, future-proofing against a sqlglot dialect bump)
    must be fully replaced by the filter subquery before the query reaches
    ``read_data``.

    Pinning both forms guards the Bug-R class on the target side. If a
    future sqlglot upgrade switches Spark rendering to ``%(tbl)s`` and the
    defensive ``replace("%(tbl)s", ...)`` ever gets removed as "dead code",
    this test will fail loudly — instead of shipping a silent Stage-2
    full-Delta-scan regression that only surfaces as inflated
    ``missing_in_source`` counts in production audit metrics.
    """
    ctx = _make_fetch_ctx(target_mock)

    _patched_fetch_target_rows(
        ctx,
        solved_hashes={1: [10, 20]},
        unsolved_sb_ids=[],
        report_type="data",
        hash_query=hash_query_template,
    )

    query = target_mock.read_data.call_args.kwargs["query"]
    # Filter subquery must be the only ``FROM`` source — no placeholder of
    # either form should reach the connector.
    assert _FAKE_TARGET_FILTER_SUBQUERY in query
    assert "_fp_filtered" in query
    assert ":tbl" not in query
    assert "%(tbl)s" not in query, (
        "Bug-R parity guard: even though Spark dialect emits ``:tbl`` today, "
        "the defensive ``replace('%(tbl)s', ...)`` at orchestrator line ~480 "
        "must remain. A future sqlglot bump that changes Spark rendering to "
        "``FROM %(tbl)s`` would otherwise silently leave the placeholder for "
        "the connector to substitute with the bare ``<schema>.<table>``, "
        "scanning the entire Delta target instead of the filtered subset and "
        "producing phantom ``missing_in_source`` counts in production "
        "``recon_metrics`` rows."
    )


def test_target_fetch_no_w2b_machinery_leakage(target_mock):
    """Defensive: no W2b experimental machinery should leak back in. Pinning
    here for the target side mirrors the source-side guard so a future
    "optimisation" attempt cannot silently re-introduce a CTE / temp-table /
    materialized-view path that requires Spark / Delta features unavailable
    on customer clusters.
    """
    ctx = _make_fetch_ctx(target_mock)

    _patched_fetch_target_rows(
        ctx,
        solved_hashes={1: [10, 20]},
        unsolved_sb_ids=[],
        report_type="data",
    )

    query = target_mock.read_data.call_args.kwargs["query"]
    assert "OFFSET 0" not in query.upper(), "OFFSET 0 fence reverted on 0.12.8"
    assert "_fp_md5_" not in query, "v2 CTE name pattern reverted on 0.12.8"
    assert " AS MATERIALIZED " not in query.upper()
    assert "CREATE TEMP TABLE" not in query.upper()
    assert "CREATE TABLE" not in query.upper()


def test_target_fetch_passes_project_all_columns_true(target_mock):
    """Stage-2 target-fetch must opt into the all-columns projection.

    Source and target MUST be in lockstep here — if source projects all columns and
    target only projects keys, ``capture_mismatch_data_and_columns`` raises because
    ``source_columns != target_columns``. Pinning the kwarg here mirrors the
    source-side guard.
    """
    from databricks.labs.lakebridge.reconcile.fingerprint import (  # pylint: disable=import-outside-toplevel
        orchestrator as orch,
    )

    fake_hash_builder = MagicMock()
    fake_hash_builder.build_query.return_value = _FAKE_TGT_HASH_QUERY

    ctx = _make_fetch_ctx(target_mock)
    with (
        patch.object(orch, "HashQueryBuilder", return_value=fake_hash_builder),
        patch.object(orch, "build_target_filter_subquery", return_value=_FAKE_TARGET_FILTER_SUBQUERY),
    ):
        orch._fetch_target_rows(  # pylint: disable=protected-access
            ctx,
            solved_hashes={1: [10, 20]},
            unsolved_sb_ids=[],
            report_type="data",
        )

    fake_hash_builder.build_query.assert_called_once()
    assert_project_all_columns_kwargs(fake_hash_builder.build_query.call_args.kwargs, side="target")
