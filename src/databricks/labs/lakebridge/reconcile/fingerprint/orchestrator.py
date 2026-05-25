import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import expr
from sqlglot import Dialect

from databricks.labs.lakebridge.config import DatabaseConfig
from databricks.labs.lakebridge.reconcile.compare import (
    _HASH_COLUMN_NAME,
    capture_mismatch_data_and_columns,
    reconcile_data as compare_reconcile_data,
)
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.fingerprint.constants import pick_sub_bucket_count
from databricks.labs.lakebridge.reconcile.fingerprint.engine import (
    DetectionResult,
    DetectionVerdict,
    detect_and_solve,
)
from databricks.labs.lakebridge.reconcile.fingerprint.exceptions import (
    UnmappedTargetColumnMappingError,
    UnsupportedDataSourceError,
)
from databricks.labs.lakebridge.reconcile.fingerprint.metadata import (
    FETCH_PATH_V1_SANDWICH,
    INELIGIBLE_COLUMN_THRESHOLDS_CONFIGURED,
    INELIGIBLE_FILTERS_CONFIGURED,
    INELIGIBLE_FLAG_DISABLED,
    INELIGIBLE_NO_JOIN_COLUMNS,
    INELIGIBLE_REPORT_TYPE_NOT_DATA,
    INELIGIBLE_TABLE_THRESHOLDS_CONFIGURED,
    INELIGIBLE_TRANSFORMS_CONFIGURED,
    INELIGIBLE_UNSUPPORTED_DIALECT,
)
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.base import FingerprintQueryBuilder
from databricks.labs.lakebridge.reconcile.fingerprint.query_builders.redshift import RedshiftFingerprintQueryBuilder
from databricks.labs.lakebridge.reconcile.fingerprint.fingerprint_hash_columns import (
    hash_columns_ordered_for_reconcile,
)
from databricks.labs.lakebridge.reconcile.fingerprint.row_count import fetch_target_row_count
from databricks.labs.lakebridge.reconcile.fingerprint.spark_target import (
    build_target_filter_subquery,
    compute_target_fingerprint,
)
from databricks.labs.lakebridge.reconcile.query_builder.hash_query import HashQueryBuilder
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from databricks.labs.lakebridge.reconcile.recon_config import Table, Schema
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    MismatchOutput,
)

logger = logging.getLogger(__name__)

# Fingerprint defaults to keeping '' distinct from NULL so the detection-side serialization
# matches the row-hash path (TRIM in expression_generator does not collapse '' to NULL).
# Flipping this to True silently disagrees with row-hash on every NULL <-> '' flip.
_DEFAULT_TREAT_EMPTY_AS_NULL = False


@dataclass(frozen=True)
class ColumnAlignment:
    column_mapping: dict[str, str] | None


@dataclass(frozen=True)
class FingerprintResult:
    verdict: DetectionVerdict
    source_rows: DataFrame | None = None
    target_rows: DataFrame | None = None
    solved_count: int = 0
    unsolved_sb_count: int = 0
    total_mismatched_sbs: int = 0
    detection_elapsed_ms: int = 0
    sub_bucket_count: int = 0
    bucket_count: int = 0
    target_row_count: int | None = None
    row_count_source: str | None = None
    fetch_path: str | None = None


@dataclass(frozen=True)
class _TierSelection:
    """Adaptive (sub_bucket_count, bucket_count) for one run.

    Source and target must use identical values or sub-bucket IDs won't align across
    the GROUP BY join.
    """

    sub_bucket_count: int
    bucket_count: int
    target_row_count: int | None
    row_count_source: str


def classify_ineligibility(
    *,
    flag_enabled: bool,
    data_source: str,
    report_type: str,
    table_conf: Table,
) -> str | None:
    """Return the ineligibility reason for the pre-check, or None when eligible.

    First-match-wins; flag/source-level reasons are surfaced before per-table config so
    adoption queries can distinguish "feature off" from "feature on but table ineligible".
    """
    if not flag_enabled:
        return INELIGIBLE_FLAG_DISABLED
    if data_source not in fingerprint_supported_sources():
        return INELIGIBLE_UNSUPPORTED_DIALECT
    if report_type not in {"data", "row", "all"}:
        return INELIGIBLE_REPORT_TYPE_NOT_DATA
    if not table_conf.join_columns:
        return INELIGIBLE_NO_JOIN_COLUMNS
    if table_conf.filters and (table_conf.filters.source or table_conf.filters.target):
        return INELIGIBLE_FILTERS_CONFIGURED
    if table_conf.transformations:
        return INELIGIBLE_TRANSFORMS_CONFIGURED
    if table_conf.column_thresholds:
        return INELIGIBLE_COLUMN_THRESHOLDS_CONFIGURED
    if table_conf.table_thresholds:
        return INELIGIBLE_TABLE_THRESHOLDS_CONFIGURED
    return None


def align_columns(
    table_conf: Table,
    src_schema: list[Schema],  # pylint: disable=unused-argument
    tgt_schema: list[Schema],
) -> ColumnAlignment | None:
    """Map a Table config to fingerprint column parameters, or raise/return None if ineligible.

    ``src_schema`` is intentionally part of the public signature for parallelism with the
    rest of the fingerprint surface (every other helper threads both schemas). Today only
    ``tgt_schema`` is consumed — for validating that every ``column_mapping`` target name
    actually exists on the target side. Catching a typo here is cheap; catching it after
    Stage-1's source-side Redshift scan ran is not — the Spark ``F.col`` resolution would
    raise mid-fetch and burn the JDBC pull.

    On an unmapped ``column_mapping`` target this raises
    ``UnmappedTargetColumnMappingError`` so the trigger layer can record the
    typed ``IneligibilityReason.UNMAPPED_TARGET_COLUMN_MAPPING`` on the
    persisted metric. The defensive guards on filters / transforms / thresholds
    keep the legacy ``None`` return — those reasons are already recorded by
    ``classify_ineligibility`` upstream so this branch is unreachable in
    practice; the guards exist solely for direct unit-test callers.
    """
    if table_conf.filters and (table_conf.filters.source or table_conf.filters.target):
        return None
    if table_conf.transformations:
        return None
    if table_conf.column_thresholds:
        return None
    if table_conf.table_thresholds:
        return None

    col_map = {cm.source_name: cm.target_name for cm in table_conf.column_mapping or []}

    if col_map:
        tgt_cols_bare = {DialectUtils.unnormalize_identifier(s.column_name).lower() for s in tgt_schema}
        for src_name, tgt_name in col_map.items():
            if DialectUtils.unnormalize_identifier(tgt_name).lower() not in tgt_cols_bare:
                # Raise (rather than silently return ``None``) so the trigger
                # layer records ``UNMAPPED_TARGET_COLUMN_MAPPING`` on the
                # persisted metric. Without the typed signal an adoption query
                # against ``ineligibility_reason`` cannot distinguish this from
                # a generic precheck decline.
                raise UnmappedTargetColumnMappingError(
                    f"column_mapping target {tgt_name!r} (mapped from {src_name!r}) "
                    f"not found in target schema for table {table_conf.source_name!r}"
                )

    return ColumnAlignment(
        column_mapping=col_map if col_map else None,
    )


def resolve_compare_key_columns(table_conf: Table) -> list[str]:
    """Return join columns for compare.reconcile_data.

    For ``row`` report type, compare.reconcile_data replaces keys with hash_value_recon.
    """
    return table_conf.join_columns or []


def fingerprint_match_output() -> DataReconcileOutput:
    """Zeroed DataReconcileOutput for a confirmed MATCH."""
    return DataReconcileOutput(
        mismatch_count=0,
        missing_in_src_count=0,
        missing_in_tgt_count=0,
        mismatch=MismatchOutput(),
        missing_in_src=None,
        missing_in_tgt=None,
    )


def build_mismatch_output(
    src_hashed: DataFrame,
    tgt_hashed: DataFrame,
    key_columns: list[str],
    report_type: str,
    persistence: AbstractReconIntermediatePersist,
) -> DataReconcileOutput:
    """Run compare.reconcile_data on rows that already have hash_value_recon.

    Bug A fix (column-level diff for fingerprint MISMATCH + report_type='all'):
    ``compare.reconcile_data`` populates ``mismatch.mismatch_df`` but never
    ``mismatch.mismatch_columns``. In the normal (non-fingerprint) path this is
    backfilled by ``Reconciliation._get_sample_data`` →
    ``capture_mismatch_data_and_columns``, but the fingerprint MISMATCH path
    bypasses ``_get_sample_data`` entirely. Because the Stage-2 fetch projects
    every hashed column (``project_all_columns=True``), ``src_hashed`` /
    ``tgt_hashed`` already carry every hashed column, so we can compute
    ``mismatch_columns`` in-place here without a second JDBC pull. Gated on
    ``report_type='all'`` + ``mismatch_count > 0`` so fingerprint MATCH and the
    zero-mismatch fast-path bear no overhead.
    """
    output = compare_reconcile_data(
        source=src_hashed,
        target=tgt_hashed,
        key_columns=key_columns,
        report_type=report_type,
        persistence=persistence,
    )

    if report_type != "all" or output.mismatch_count == 0:
        return output

    # The fingerprint frames carry ``hash_value_recon``; treat it as a
    # derived/synthetic column - rows that hash differently are precisely the
    # mismatched rows, so leaving it in would always show as "mismatched" and
    # inflate ``mismatch_columns`` with a non-source-column.
    src_for_capture = src_hashed.drop(_HASH_COLUMN_NAME) if _HASH_COLUMN_NAME in src_hashed.columns else src_hashed
    tgt_for_capture = tgt_hashed.drop(_HASH_COLUMN_NAME) if _HASH_COLUMN_NAME in tgt_hashed.columns else tgt_hashed

    capture = capture_mismatch_data_and_columns(
        source=src_for_capture,
        target=tgt_for_capture,
        key_columns=key_columns,
    )

    # Build the wide ``mismatch_df`` consumed by ``recon_capture._create_map_column``.
    # ``capture.mismatch_df`` is an INNER JOIN over the Stage-2 fetched subset,
    # which means it contains EVERY src/tgt pair that share a key, including
    # rows that turned out to match column-by-column (Stage-1 only proves the
    # sub-bucket has *some* mismatch; the row-by-row check happens here).
    # We:
    #   1. Filter to rows where at least one ``<col>_match`` is False (the
    #      genuine row-level mismatches).
    #   2. Append a per-row ``mismatch_columns`` STRING column with the
    #      comma-separated list of cols where ``<col>_match=False``. This is
    #      the field downstream tooling and audit harnesses key on; without
    #      it the recon_details rows look indistinguishable from full-row
    #      MISMATCHes and the column-level diff is unrecoverable.
    final_mismatch_df = capture.mismatch_df
    match_cols = [c for c in final_mismatch_df.columns if c.endswith("_match")] if final_mismatch_df is not None else []
    if final_mismatch_df is not None and match_cols:
        # ``compare._get_mismatch_df`` builds ``<col>_match`` with bare ``=``
        # (NOT null-safe). For mismatches that involve NULL on one side
        # (``NULL <-> value``, ``value <-> NULL``) the resulting ``_match`` is
        # NULL not FALSE; for unchanged NULL columns (``NULL <-> NULL``, e.g.
        # ``notes`` left untouched while the row mutates ``is_priority``) it is
        # ALSO NULL despite being a match. ``NOT NULL`` evaluates to NULL in
        # SQL so a naive ``NOT _match`` filter silently drops *every* such
        # row, and a naive ``COALESCE(_match, false)`` flips both directions
        # (drops legit row, but inflates per-row ``mismatch_columns`` with
        # NULL-NULL columns - over-reporting).
        #
        # The correct fix is null-safe equality (``<=>``): ``NULL <=> NULL``
        # is TRUE (match), ``NULL <=> value`` is FALSE (mismatch). We recompute
        # every ``<col>_match`` from the existing ``_base`` / ``_compare``
        # columns using ``<=>``, which yields a non-null BOOLEAN. The
        # downstream filter/case-when then work without COALESCE wrappers and
        # match the Python-side ``_get_mismatch_columns`` semantics, so the
        # table-level ``recon_metrics.column_comparison.mismatch_columns`` and
        # the per-row ``recon_details`` agree.
        for match_col in match_cols:
            stem = match_col[: -len("_match")]
            base_col = f"{stem}_base"
            compare_col = f"{stem}_compare"
            if base_col in final_mismatch_df.columns and compare_col in final_mismatch_df.columns:
                final_mismatch_df = final_mismatch_df.withColumn(match_col, expr(f"`{base_col}` <=> `{compare_col}`"))

        not_all_match = " OR ".join(f"NOT `{c}`" for c in match_cols)
        diff_case_exprs = ", ".join(f"CASE WHEN NOT `{c}` THEN '{c[: -len('_match')]}' END" for c in match_cols)
        final_mismatch_df = final_mismatch_df.filter(expr(not_all_match)).withColumn(
            "mismatch_columns", expr(f"concat_ws(',', {diff_case_exprs})")
        )

    return DataReconcileOutput(
        mismatch_count=output.mismatch_count,
        missing_in_src_count=output.missing_in_src_count,
        missing_in_tgt_count=output.missing_in_tgt_count,
        missing_in_src=output.missing_in_src,
        missing_in_tgt=output.missing_in_tgt,
        mismatch=MismatchOutput(
            mismatch_df=final_mismatch_df,
            mismatch_columns=capture.mismatch_columns,
        ),
        threshold_output=output.threshold_output,
    )


def _resolve_detection_columns(
    table_conf: Table,
    src_schema: list[Schema],
    source: DataSource,
) -> list[Schema] | None:
    """Resolve hash columns against the source schema, or None to skip fingerprint."""
    hash_col_names = hash_columns_ordered_for_reconcile(table_conf, src_schema, "source", source)
    if not hash_col_names:
        logger.warning("Fingerprint: no hash columns resolved — skipping")
        return None

    # Schema entries are ANSI-delimited via _map_meta_column; user-supplied join_columns
    # are bare. Strip and lowercase on both sides so quoting and casing round-trip.
    by_name = {DialectUtils.unnormalize_identifier(s.column_name).lower(): s for s in src_schema}
    detection_cols: list[Schema] = []
    for name in hash_col_names:
        schema_entry = by_name.get(DialectUtils.unnormalize_identifier(name).lower())
        if schema_entry is None:
            logger.warning(f"Fingerprint: hash column '{name}' missing from source schema — skipping")
            return None
        detection_cols.append(schema_entry)
    return detection_cols


def _select_tier(
    spark: SparkSession,
    database_config: DatabaseConfig,
    table_conf: Table,
    override_row_count: int | None = None,
) -> _TierSelection:
    """Pick (sub_bucket_count, bucket_count) from the target Delta row count.

    Falls back to static defaults when the target is non-Delta or stats are missing.
    """
    row_count_result = fetch_target_row_count(
        spark,
        catalog=database_config.target_catalog,
        schema=database_config.target_schema,
        table=table_conf.target_name,
        override_row_count=override_row_count,
    )
    sub_bucket_count, bucket_count = pick_sub_bucket_count(row_count_result.row_count)
    return _TierSelection(
        sub_bucket_count=sub_bucket_count,
        bucket_count=bucket_count,
        target_row_count=row_count_result.row_count,
        row_count_source=row_count_result.source.value,
    )


def _run_detection_phase(
    source: DataSource,
    spark: SparkSession,
    database_config: DatabaseConfig,
    table_conf: Table,
    detection_cols: list[Schema],
    column_mapping: dict[str, str] | None,
    query_builder: FingerprintQueryBuilder,
    tier: _TierSelection,
    treat_empty_as_null: bool,
) -> tuple[DetectionResult, int]:
    """Run detection aggregates on both sides; return (result, elapsed_ms).

    ``treat_empty_as_null`` flows in from the orchestrator (config-driven via
    ``ReconcileConfig.fingerprint_treat_empty_as_null``) so source and target stay in
    lockstep — silently disagreeing here causes systemic Stage-1 mismatch on every
    NULL/'' cell.
    """
    start_time = time.monotonic()
    source_detection_sql = query_builder.build_detection_sql(
        schema=database_config.source_schema,
        table=table_conf.source_name,
        columns=detection_cols,
        column_mapping=column_mapping,
        sub_bucket_count=tier.sub_bucket_count,
        bucket_count=tier.bucket_count,
    )
    source_agg_df = source.read_data(
        catalog=database_config.source_catalog,
        schema=database_config.source_schema,
        table=table_conf.source_name,
        query=source_detection_sql,
        options=table_conf.jdbc_reader_options,
    )
    target_agg_df = compute_target_fingerprint(
        spark=spark,
        catalog=database_config.target_catalog,
        schema=database_config.target_schema,
        table=table_conf.target_name,
        columns=detection_cols,
        column_mapping=column_mapping,
        sub_bucket_count=tier.sub_bucket_count,
        bucket_count=tier.bucket_count,
        treat_empty_as_null=treat_empty_as_null,
    )
    detection = detect_and_solve(source_agg_df, target_agg_df)
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    return detection, elapsed_ms


@dataclass(frozen=True)
class _FetchContext:
    """Inputs the fetch phase needs."""

    source: DataSource
    target: DataSource
    source_engine: Dialect
    database_config: DatabaseConfig
    table_conf: Table
    src_schema: list[Schema]
    tgt_schema: list[Schema]
    detection_cols: list[Schema]
    column_mapping: dict[str, str] | None
    query_builder: FingerprintQueryBuilder
    tier: _TierSelection
    # Mirrors the Stage-1 ``treat_empty_as_null`` flag so Stage-2's target-side filter
    # subquery cannot drift apart — see ``_run_detection_phase`` for the contract. No
    # default: the orchestrator is the single source of truth (via
    # ``ReconcileConfig.fingerprint_treat_empty_as_null``) and must always pass it
    # explicitly so the four call sites cannot diverge silently.
    treat_empty_as_null: bool


# sqlglot renders the table placeholder per dialect: Spark/Databricks emits ``:tbl``,
# Postgres-family dialects emit ``%(tbl)s`` (pyformat). Substitute both forms.
_TBL_PLACEHOLDERS = (":tbl", "%(tbl)s")


def _substitute_tbl_placeholder(query: str, replacement: str) -> str:
    for placeholder in _TBL_PLACEHOLDERS:
        query = query.replace(placeholder, replacement)
    return query


def _fetch_source_rows(
    ctx: _FetchContext,
    solved_hashes: dict[int, list[int]],
    unsolved_sb_ids: list[int],
    report_type: str,
) -> tuple[DataFrame, str]:
    """Fetch source rows for Stage-2 reconcile.

    Single statement: filter subquery is injected into the hash query's table
    placeholder, producing one query that filters by sub-bucket and projects
    LOWER(SHA2(...,256)) AS hash_value_recon. Only the hash and join columns
    cross JDBC.
    """
    source_filter_subquery = ctx.query_builder.build_source_filter_subquery(
        schema=ctx.database_config.source_schema,
        table=ctx.table_conf.source_name,
        columns=ctx.detection_cols,
        sub_bucket_count=ctx.tier.sub_bucket_count,
        solved_hashes=solved_hashes,
        unsolved_sb_ids=unsolved_sb_ids,
    )
    # Project every hashed column, not just hash + join keys, so the downstream
    # compare layer can populate ``mismatch_columns`` without a second round-trip
    # to Redshift. Off in normal Lakebridge mode; only the fingerprint Stage-2
    # fetch flips this on.
    src_hash_query = HashQueryBuilder(
        ctx.table_conf, ctx.src_schema, "source", ctx.source_engine, ctx.source
    ).build_query(report_type=report_type, project_all_columns=True)
    src_filtered_query = _substitute_tbl_placeholder(src_hash_query, source_filter_subquery)

    df = ctx.source.read_data(
        catalog=ctx.database_config.source_catalog,
        schema=ctx.database_config.source_schema,
        table=ctx.table_conf.source_name,
        query=src_filtered_query,
        options=ctx.table_conf.jdbc_reader_options,
    )
    return df, FETCH_PATH_V1_SANDWICH


def _fetch_target_rows(
    ctx: _FetchContext,
    solved_hashes: dict[int, list[int]],
    unsolved_sb_ids: list[int],
    report_type: str,
) -> DataFrame:
    """Fetch target rows for Stage-2 reconcile via the Spark-side filter subquery."""
    # See ``_fetch_source_rows`` for the project_all_columns rationale.
    # Source and target MUST stay in lockstep: if source projects all columns and
    # target only projects keys, ``capture_mismatch_data_and_columns`` will raise
    # because ``source_columns != target_columns``.
    tgt_hash_query = HashQueryBuilder(
        ctx.table_conf, ctx.tgt_schema, "target", ctx.source_engine, ctx.target
    ).build_query(report_type=report_type, project_all_columns=True)
    tgt_filter_subquery = build_target_filter_subquery(
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
    tgt_filtered_query = _substitute_tbl_placeholder(tgt_hash_query, tgt_filter_subquery)
    return ctx.target.read_data(
        catalog=ctx.database_config.target_catalog,
        schema=ctx.database_config.target_schema,
        table=ctx.table_conf.target_name,
        query=tgt_filtered_query,
        options=None,
    )


def _fetch_source_and_target_rows(
    ctx: _FetchContext,
    solved_hashes: dict[int, list[int]],
    unsolved_sb_ids: list[int],
    report_type: str,
) -> tuple[DataFrame, str, DataFrame]:
    """Run Stage-2 source and target fetches in parallel (B3).

    Source fetch is JDBC-bound (Redshift round trip + scan), target fetch is a
    Spark filter-subquery against the cached Delta table. They share zero state
    (different connectors, different DataFrames, immutable inputs) so dispatching
    on two driver threads lets the JDBC pull overlap with the target Spark job
    submission instead of running serially.

    Note: each fetch returns lazily — Spark's DAG isn't materialised until a
    downstream action collects. So the wall-clock win comes from overlapping the
    JDBC pull (which connectors typically force-collect via ``read_data``)
    with the target query planning + initial Spark stage submission. On Spark
    cluster execution itself, both fetches still parallelize across the
    cluster's executors as before; this helper only addresses driver-side
    serialization.

    Failure semantics: ``ThreadPoolExecutor.__exit__`` joins both futures, and
    ``future.result()`` re-raises any exception from the worker thread on the
    caller's stack — so behaviour is identical to the serial version on errors.
    A failure in either fetch immediately aborts the precheck, just like before.

    Sibling-future cancellation: if the source fetch fails first, the target
    fetch keeps running until it completes naturally — Python's
    ``Future.cancel()`` is a no-op once the worker has started, so there is no
    cheap way to interrupt a Spark job submission mid-flight from the driver.
    The trigger layer's exception-catch wraps this whole block, so any work
    that completes after the first failure is discarded and the ``with`` block
    waits at most one extra fetch's worth of time before returning. The
    two-thread cap means the overshoot is bounded; documenting this here so
    future readers do not add a ``cancel()`` call expecting it to interrupt
    the running Spark/JDBC submission.
    """
    # max_workers=2 because we have exactly two independent fetches. Naming the
    # threads helps when debugging stuck JDBC pulls in production thread dumps.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fp-stage2") as pool:
        src_future = pool.submit(_fetch_source_rows, ctx, solved_hashes, unsolved_sb_ids, report_type)
        tgt_future = pool.submit(_fetch_target_rows, ctx, solved_hashes, unsolved_sb_ids, report_type)
        src_data, fetch_path = src_future.result()
        tgt_data = tgt_future.result()
    return src_data, fetch_path, tgt_data


def run_fingerprint_precheck(  # pylint: disable=too-many-locals,too-many-arguments
    source: DataSource,
    target: DataSource,
    spark: SparkSession,
    source_engine: Dialect,
    database_config: DatabaseConfig,
    table_conf: Table,
    src_schema: list[Schema],
    tgt_schema: list[Schema],
    report_type: str,
    data_source: str,
    treat_empty_as_null: bool = _DEFAULT_TREAT_EMPTY_AS_NULL,
    target_row_count_override: int | None = None,
    recon_id: str | None = None,
) -> FingerprintResult | None:
    """Execute the fingerprint pre-check for one table pair, or None if ineligible.

    On MISMATCH, the result carries pre-fetched source/target DataFrames already
    projected with hash_value_recon, ready for compare.reconcile_data.

    Eligibility (flag, dialect, report_type, ``join_columns``, filters, transforms,
    thresholds) is the contract of ``classify_ineligibility`` at the trigger layer;
    callers must run that gate first. Bypassing it is undefined behaviour.

    ``recon_id`` is woven into the high-level log prefix so a multi-table run can
    be traced from a single grep against the cluster logs without correlating by
    timestamp alone.
    """
    log_tag = f"Fingerprint[recon_id={recon_id}]" if recon_id else "Fingerprint"
    alignment = align_columns(table_conf, src_schema, tgt_schema)
    if alignment is None:
        logger.info(f"{log_tag}: table '{table_conf.source_name}' ineligible — skipping pre-check")
        return None

    detection_cols = _resolve_detection_columns(table_conf, src_schema, source)
    if detection_cols is None:
        return None

    query_builder = get_query_builder(data_source, treat_empty_as_null=treat_empty_as_null)

    # Same tier MUST be used by detection and fetch — Stage-2's filter modulus
    # has to match Stage-1's GROUP BY modulus or solver IDs won't align. The
    # ``target_row_count_override`` short-circuits ``DESCRIBE DETAIL`` so
    # non-Delta targets (or stale-stats Delta) still land on the right tier.
    tier = _select_tier(spark, database_config, table_conf, override_row_count=target_row_count_override)

    detection, elapsed_ms = _run_detection_phase(
        source,
        spark,
        database_config,
        table_conf,
        detection_cols,
        alignment.column_mapping,
        query_builder,
        tier,
        treat_empty_as_null,
    )

    if detection.verdict == "MATCH":
        return FingerprintResult(
            verdict="MATCH",
            detection_elapsed_ms=elapsed_ms,
            sub_bucket_count=tier.sub_bucket_count,
            bucket_count=tier.bucket_count,
            target_row_count=tier.target_row_count,
            row_count_source=tier.row_count_source,
        )

    if detection.systemic_mismatch:
        logger.info(f"{log_tag}: systemic mismatch — deferring to full pipeline")
        return None

    solved_hashes = collect_solved_hashes(detection)
    unsolved_sb_ids = detection.unsolved_sb_ids
    if not solved_hashes and not unsolved_sb_ids:
        return None

    fetch_ctx = _FetchContext(
        source=source,
        target=target,
        source_engine=source_engine,
        database_config=database_config,
        table_conf=table_conf,
        src_schema=src_schema,
        tgt_schema=tgt_schema,
        detection_cols=detection_cols,
        column_mapping=alignment.column_mapping,
        query_builder=query_builder,
        tier=tier,
        treat_empty_as_null=treat_empty_as_null,
    )
    src_data, fetch_path, tgt_data = _fetch_source_and_target_rows(
        fetch_ctx, solved_hashes, unsolved_sb_ids, report_type
    )

    return FingerprintResult(
        verdict="MISMATCH",
        source_rows=src_data,
        target_rows=tgt_data,
        solved_count=len(detection.solved_results),
        unsolved_sb_count=len(detection.unsolved_sb_ids),
        total_mismatched_sbs=detection.total_mismatched_sbs,
        detection_elapsed_ms=elapsed_ms,
        sub_bucket_count=tier.sub_bucket_count,
        bucket_count=tier.bucket_count,
        target_row_count=tier.target_row_count,
        row_count_source=tier.row_count_source,
        fetch_path=fetch_path,
    )


# Adding a new source = one entry here plus a new FingerprintQueryBuilder subclass.
_QUERY_BUILDERS: dict[str, type[FingerprintQueryBuilder]] = {
    "redshift": RedshiftFingerprintQueryBuilder,
}


def get_query_builder(
    data_source: str,
    *,
    treat_empty_as_null: bool = _DEFAULT_TREAT_EMPTY_AS_NULL,
) -> FingerprintQueryBuilder:
    """Return the registered builder for ``data_source``.

    Raises ``UnsupportedDataSourceError`` (a ValueError) when no builder is registered;
    callers should pre-flight via ``fingerprint_supported_sources()``.

    ``treat_empty_as_null`` is threaded so the source-side serialiser stays in lockstep
    with the target-side ``compute_target_fingerprint`` / ``build_target_filter_subquery``
    calls — disagreement here causes systemic Stage-1 mismatch on every NULL/'' cell.
    """
    try:
        builder_cls = _QUERY_BUILDERS[data_source]
    except KeyError as e:
        raise UnsupportedDataSourceError(
            f"No fingerprint query builder registered for data_source={data_source!r}. "
            f"Supported: {sorted(_QUERY_BUILDERS)}"
        ) from e
    return builder_cls(treat_empty_as_null=treat_empty_as_null)


def fingerprint_supported_sources() -> frozenset[str]:
    """Sources with a registered FingerprintQueryBuilder."""
    return frozenset(_QUERY_BUILDERS)


def collect_solved_hashes(detection: DetectionResult) -> dict[int, list[int]]:
    """Merge solved source and target hashes per sub-bucket into a single lookup.

    Multiple ``SolveResult`` rows can share a ``sub_bucket_id``; the same hash can also
    appear on both the source and target side. Dedupe via ``set`` so the dict stays
    O(distinct hashes) for memory; sort on the way out for deterministic SQL emission
    (``build_fingerprint_where_clause`` already dedupes the IN-list, but the dict is
    held driver-side until then and dominates memory at the 50 K-sub-bucket cap).
    """
    accum: dict[int, set[int]] = {}
    for solve in detection.solved_results:
        if not solve.source_hashes and not solve.target_hashes:
            continue
        bucket = accum.setdefault(solve.sub_bucket_id, set())
        bucket.update(solve.source_hashes)
        bucket.update(solve.target_hashes)
    return {sb_id: sorted(hashes) for sb_id, hashes in accum.items()}
