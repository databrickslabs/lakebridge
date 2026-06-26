"""Source/target compare and reconciliation.

Three flows share ``_aliased_join`` / ``_join_prepare_persist`` / ``_filter_to_value_mismatches`` where noted:

1. **Hash row reconcile** — ``reconcile_data``: full outer join on keys, prefixed ``src``/``tgt``
   columns, compare ``hash_value_recon``, missing-side and value-mismatch helpers.
2. **Aggregate reconcile** — ``prepare_persisted_aggregate_join`` then
   ``reconcile_agg_data_per_rule``: full or cross join, ``ColumnMapping`` pairs,
   ``_mismatch_rows_for_aggregate_mappings``.
3. **Capture (column-level)** — ``capture_mismatch_data_and_columns`` / ``_get_mismatch_df``:
   inner join on keys (aliases ``base``/``compare``), per-column ``_base``/``_compare``/``_match``
   projections; keeps all key-matched rows with booleans (does not filter to mismatches only).

See `lakebridge#745` (Data Compare consolidation).
"""

import logging
from collections.abc import Callable
from functools import reduce
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, expr, lit

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.exception import ColumnMismatchException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    AbstractReconIntermediatePersist,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    MismatchOutput,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    AggregateRule,
    ColumnMapping,
)

logger = logging.getLogger(__name__)

_HASH_COLUMN_NAME = "hash_value_recon"
_SAMPLE_ROWS = 50

_CAPTURE_SOURCE_ALIAS = "base"
_CAPTURE_TARGET_ALIAS = "compare"


def _raise_column_mismatch_exception(msg: str, source_missing: list[str], target_missing: list[str]) -> Exception:
    error_msg = (
        f"{msg}\n"
        f"columns missing in source: {','.join(source_missing) if source_missing else None}\n"
        f"columns missing in target: {','.join(target_missing) if target_missing else None}\n"
    )
    return ColumnMismatchException(error_msg)


def _generate_join_condition(source_alias, target_alias, key_columns):
    conditions = [
        col(f"{source_alias}.{DialectUtils.ansi_normalize_identifier(key_column)}").eqNullSafe(
            col(f"{target_alias}.{DialectUtils.ansi_normalize_identifier(key_column)}")
        )
        for key_column in key_columns
    ]
    return reduce(lambda a, b: a & b, conditions)


def _build_column_selector(table_name, column_name):
    alias = DialectUtils.ansi_normalize_identifier(f"{table_name}_{DialectUtils.unnormalize_identifier(column_name)}")
    return f'{table_name}.{DialectUtils.ansi_normalize_identifier(column_name)} as {alias}'


def _aliased_join(
    source: DataFrame,
    target: DataFrame,
    *,
    source_alias: str,
    target_alias: str,
    how: str,
    on=None,
) -> DataFrame:
    src = source.alias(source_alias)
    tgt = target.alias(target_alias)
    if how == "cross":
        return src.join(other=tgt, how="cross")
    if on is None:
        raise ValueError("join condition 'on' is required when how is not 'cross'")
    return src.join(other=tgt, on=on, how=how)


def _inner_join_for_capture_mismatch(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str],
) -> DataFrame:
    """Inner join on shared key column names (capture / column-level mismatch path, issue #745)."""
    return _aliased_join(
        source,
        target,
        source_alias=_CAPTURE_SOURCE_ALIAS,
        target_alias=_CAPTURE_TARGET_ALIAS,
        how="inner",
        on=key_columns,
    )


def _persist_reconcile_dataframe(df: DataFrame, persistence: AbstractReconIntermediatePersist) -> DataFrame:
    return persistence.write_and_read_df_with_volumes(df)


def _join_prepare_persist(
    source: DataFrame,
    target: DataFrame,
    persistence: AbstractReconIntermediatePersist,
    *,
    source_alias: str,
    target_alias: str,
    how: str,
    on=None,
    prepare: Callable[[DataFrame], DataFrame],
) -> DataFrame:
    joined = _aliased_join(
        source,
        target,
        source_alias=source_alias,
        target_alias=target_alias,
        how=how,
        on=on,
    )
    return _persist_reconcile_dataframe(prepare(joined), persistence)


def _select_prefixed_columns_for_hash_reconcile(
    joined: DataFrame,
    *,
    source: DataFrame,
    target: DataFrame,
    source_alias: str,
    target_alias: str,
) -> DataFrame:
    return joined.selectExpr(
        *[f'{_build_column_selector(source_alias, col_name)}' for col_name in source.columns],
        *[f'{_build_column_selector(target_alias, col_name)}' for col_name in target.columns],
    )


def _select_aggregate_joined_columns(
    joined: DataFrame,
    *,
    source: DataFrame,
    target: DataFrame,
) -> DataFrame:
    joined_cols = source.columns + target.columns
    normalized_joined_cols = [DialectUtils.ansi_normalize_identifier(c) for c in joined_cols]
    return joined.select(*normalized_joined_cols)


def prepare_persisted_aggregate_join(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str] | None,
    persistence: AbstractReconIntermediatePersist,
) -> DataFrame:
    """Full/cross join source and target for aggregate reconciliation, normalize columns, persist.

    Uses the same ``_join_prepare_persist`` pipeline as hash reconciliation (issue #745).
    Replaces the former ``join_aggregate_data`` entry point.
    """
    source_alias = "src"
    target_alias = "tgt"
    if key_columns:
        how = "full"
        join_condition = _generate_agg_join_condition(source_alias, target_alias, key_columns)
    else:
        how = "cross"
        join_condition = None
    return _join_prepare_persist(
        source,
        target,
        persistence,
        source_alias=source_alias,
        target_alias=target_alias,
        how=how,
        on=join_condition,
        prepare=lambda joined: _select_aggregate_joined_columns(joined, source=source, target=target),
    )


def _build_mismatch_column(table, column):
    return col(DialectUtils.ansi_normalize_identifier(column)).alias(
        DialectUtils.unnormalize_identifier(column.replace(f'{table}_', '').lower())
    )


def _mismatch_projection_for_prefixed_columns(df: DataFrame, side_alias: str):
    return [
        _build_mismatch_column(side_alias, col_name) for col_name in df.columns if col_name.startswith(f"{side_alias}_")
    ]


def _joined_rows_missing_on_side(
    df: DataFrame,
    *,
    absent_side_alias: str,
    present_side_alias: str,
    compare_basename: str = _HASH_COLUMN_NAME,
) -> DataFrame:
    """Rows where the join matched only one side: ``absent`` side has null ``{alias}_{compare_basename}``."""
    return (
        df.filter(col(f"{absent_side_alias}_{compare_basename}").isNull())
        .select(*_mismatch_projection_for_prefixed_columns(df, present_side_alias))
        .drop(compare_basename)
    )


def _filter_to_value_mismatches(
    df: DataFrame,
    *,
    values_equal,
    match_flag_col: str,
    row_predicate=None,
) -> DataFrame:
    """Keep rows where ``values_equal`` is false (after optional ``row_predicate``).

    Shared by hash reconcile (single pair of columns, both non-null) and aggregate
    reconcile (reduced AND of source/target column equalities).
    """
    out = df
    if row_predicate is not None:
        out = out.filter(row_predicate)
    return out.withColumn(match_flag_col, values_equal).filter(col(match_flag_col) == lit(False))


def _value_mismatch_where_both_present(
    df: DataFrame,
    left_col: str,
    right_col: str,
    *,
    match_col_name: str,
) -> DataFrame:
    """Hash-style compare: both columns non-null, keep rows where they differ."""
    presence = col(left_col).isNotNull() & col(right_col).isNotNull()
    return _filter_to_value_mismatches(
        df,
        row_predicate=presence,
        values_equal=col(left_col) == col(right_col),
        match_flag_col=match_col_name,
    )


def _mismatch_rows_for_prefixed_compare_column(
    df: DataFrame,
    *,
    source_alias: str,
    target_alias: str,
    compare_basename: str,
    match_flag_col: str,
) -> DataFrame:
    """Value mismatches when both sides have ``{alias}_{compare_basename}``; project source-side prefixed columns."""
    src_c = f"{source_alias}_{compare_basename}"
    tgt_c = f"{target_alias}_{compare_basename}"
    return (
        _value_mismatch_where_both_present(df, src_c, tgt_c, match_col_name=match_flag_col)
        .select(*_mismatch_projection_for_prefixed_columns(df, source_alias))
        .drop(compare_basename)
    )


def _data_reconcile_output(
    *,
    mismatch_df: DataFrame | None,
    missing_in_src: DataFrame,
    missing_in_tgt: DataFrame,
) -> DataReconcileOutput:
    mismatch_count = mismatch_df.count() if mismatch_df is not None else 0
    return DataReconcileOutput(
        mismatch_count=mismatch_count,
        missing_in_src_count=missing_in_src.count(),
        missing_in_tgt_count=missing_in_tgt.count(),
        missing_in_src=missing_in_src.limit(_SAMPLE_ROWS),
        missing_in_tgt=missing_in_tgt.limit(_SAMPLE_ROWS),
        mismatch=MismatchOutput(mismatch_df=mismatch_df),
    )


def reconcile_data(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str],
    report_type: str,
    persistence: AbstractReconIntermediatePersist,
) -> DataReconcileOutput:
    source_alias = "src"
    target_alias = "tgt"
    if report_type not in {"data", "all"}:
        key_columns = [_HASH_COLUMN_NAME]
    df = _join_prepare_persist(
        source,
        target,
        persistence,
        source_alias=source_alias,
        target_alias=target_alias,
        how="full",
        on=_generate_join_condition(source_alias, target_alias, key_columns),
        prepare=lambda joined: _select_prefixed_columns_for_hash_reconcile(
            joined,
            source=source,
            target=target,
            source_alias=source_alias,
            target_alias=target_alias,
        ),
    )
    # Checkpoint after joining source and target to backpressure

    mismatch = _get_mismatch_data(df, source_alias, target_alias) if report_type in {"all", "data"} else None

    missing_in_src = _joined_rows_missing_on_side(df, absent_side_alias=source_alias, present_side_alias=target_alias)
    missing_in_tgt = _joined_rows_missing_on_side(df, absent_side_alias=target_alias, present_side_alias=source_alias)
    return _data_reconcile_output(
        mismatch_df=mismatch,
        missing_in_src=missing_in_src,
        missing_in_tgt=missing_in_tgt,
    )


def _get_mismatch_data(df: DataFrame, src_alias: str, tgt_alias: str) -> DataFrame:
    return _mismatch_rows_for_prefixed_compare_column(
        df,
        source_alias=src_alias,
        target_alias=tgt_alias,
        compare_basename=_HASH_COLUMN_NAME,
        match_flag_col="hash_match",
    )


def _build_capture_df(df: DataFrame) -> DataFrame:
    columns = [
        col(DialectUtils.ansi_normalize_identifier(column)).alias(DialectUtils.unnormalize_identifier(column))
        for column in df.columns
    ]
    return df.select(*columns)


def capture_mismatch_data_and_columns(source: DataFrame, target: DataFrame, key_columns: list[str]) -> MismatchOutput:
    """Inner-join capture with per-column ``_match`` flags (not full-outer hash reconcile). Shares ``_aliased_join``."""
    source_df = _build_capture_df(source)
    target_df = _build_capture_df(target)
    unnormalized_key_columns = [DialectUtils.unnormalize_identifier(column) for column in key_columns]

    source_columns = source_df.columns
    target_columns = target_df.columns

    if source_columns != target_columns:
        message = "source and target should have same columns for capturing the mismatch data"
        source_missing = [column for column in target_columns if column not in source_columns]
        target_missing = [column for column in source_columns if column not in target_columns]
        raise _raise_column_mismatch_exception(message, source_missing, target_missing)

    check_columns = [column for column in source_columns if column not in unnormalized_key_columns]
    mismatch_df = _get_mismatch_df(source_df, target_df, unnormalized_key_columns, check_columns)
    # TODO write `mismatch_df` to delta
    mismatch_columns = _get_mismatch_columns(mismatch_df, check_columns)
    return MismatchOutput(mismatch_df, mismatch_columns)


def _get_mismatch_columns(df: DataFrame, columns: list[str]):
    # Collect the DataFrame to a local variable
    local_df = df.collect()
    mismatch_columns = []
    for column in columns:
        # Check if any row has False in the column
        if any(not row[column + "_match"] for row in local_df):
            mismatch_columns.append(column)
    return mismatch_columns


def _normalize_mismatch_df_col(column, suffix):
    unnormalized = DialectUtils.unnormalize_identifier(column) + suffix
    return DialectUtils.ansi_normalize_identifier(unnormalized)


def _unnormalize_mismatch_df_col(column, suffix):
    unnormalized = DialectUtils.unnormalize_identifier(column) + suffix
    return unnormalized


def _capture_mismatch_base_compare_projections(column_list: list[str]):
    source_alias, compare_alias = _CAPTURE_SOURCE_ALIAS, _CAPTURE_TARGET_ALIAS
    source_aliased = [
        col(f"{source_alias}." + DialectUtils.ansi_normalize_identifier(column)).alias(
            _unnormalize_mismatch_df_col(column, "_base")
        )
        for column in column_list
    ]
    target_aliased = [
        col(f"{compare_alias}." + DialectUtils.ansi_normalize_identifier(column)).alias(
            _unnormalize_mismatch_df_col(column, "_compare")
        )
        for column in column_list
    ]
    return source_aliased, target_aliased


def _capture_mismatch_per_column_match_exprs(column_list: list[str]):
    return [
        expr(f"{_normalize_mismatch_df_col(column, '_base')}=={_normalize_mismatch_df_col(column, '_compare')}").alias(
            _unnormalize_mismatch_df_col(column, "_match")
        )
        for column in column_list
    ]


def _get_mismatch_df(source: DataFrame, target: DataFrame, key_columns: list[str], column_list: list[str]):
    source_aliased, target_aliased = _capture_mismatch_base_compare_projections(column_list)
    match_expr = _capture_mismatch_per_column_match_exprs(column_list)
    key_cols = [col(DialectUtils.ansi_normalize_identifier(column)) for column in key_columns]
    select_expr = key_cols + source_aliased + target_aliased + match_expr

    logger.info(f"KEY COLUMNS: {key_columns}")
    logger.info(f"SELECT COLUMNS: {select_expr}")

    joined = _inner_join_for_capture_mismatch(source, target, key_columns)
    mismatch_df = joined.select(*select_expr)

    compare_columns = [
        DialectUtils.ansi_normalize_identifier(column) for column in mismatch_df.columns if column not in key_columns
    ]
    return mismatch_df.select(*key_cols + sorted(compare_columns))


def _generate_agg_join_condition(source_alias: str, target_alias: str, key_columns: list[str]):
    join_columns: list[ColumnMapping] = [
        ColumnMapping(
            source_name=DialectUtils.ansi_normalize_identifier(
                f"source_group_by_{DialectUtils.unnormalize_identifier(key_col)}"
            ),
            target_name=DialectUtils.ansi_normalize_identifier(
                f"target_group_by_{DialectUtils.unnormalize_identifier(key_col)}"
            ),
        )
        for key_col in key_columns
    ]
    conditions = [
        col(f"{source_alias}.{mapping.source_name}").eqNullSafe(col(f"{target_alias}.{mapping.target_name}"))
        for mapping in join_columns
    ]
    return reduce(lambda a, b: a & b, conditions)


def _agg_conditions(
    cols: list[ColumnMapping] | None,
    condition_type: str = "group_filter",
    op_type: str = "and",
):
    """
    Generate conditions for aggregated data comparison based on the condition type
    and reduces it based on the operator (and, or)

    e.g.,  cols = [(source_min_col1, target_min_col1)]
              1. condition_type = "group_filter"
                    source_group_by_col1 is not null and target_group_by_col1 is not null
              2. condition_type = "select"
                    source_min_col1 == target_min_col1
              3. condition_type = "missing_in_src"
                    source_min_col1 is null
              4. condition_type = "missing_in_tgt"
                      target_min_col1 is null

    :param cols:  List of columns to compare
    :param condition_type:  Type of condition to generate
    :param op_type: and, or
    :return:  Reduced column expressions
    """
    assert cols, "Columns must be specified for aggregation conditions"

    if condition_type == "group_filter":
        conditions_list = [
            (col(f"{mapping.source_name}").isNotNull() & col(f"{mapping.target_name}").isNotNull())
            for mapping in cols  # TODO
        ]
    elif condition_type == "select":
        conditions_list = [col(f"{mapping.source_name}") == col(f"{mapping.target_name}") for mapping in cols]
    elif condition_type == "missing_in_src":
        conditions_list = [col(f"{mapping.source_name}").isNull() for mapping in cols]
    elif condition_type == "missing_in_tgt":
        conditions_list = [col(f"{mapping.target_name}").isNull() for mapping in cols]
    else:
        raise ValueError(f"Invalid condition type: {condition_type}")

    return reduce(lambda a, b: a & b if op_type == "and" else a | b, conditions_list)


def _generate_match_columns(select_cols: list[ColumnMapping]):
    """
    Generate match columns for the given select columns
    e.g.,  select_cols = [(source_min_col1, target_min_col1), (source_count_col3, target_count_col3)]
            |--------------------------------------|---------------------|
           |               match_min_col1                      |  match_count_col3 |
           |--------------------------------------|--------------------|
             source_min_col1 == target_min_col1 | source_count_col3 == target_count_col3
           --------------------------------------|---------------------|

    :param select_cols:
    :return:
    """
    items = []
    for mapping in select_cols:
        match_col_name = mapping.source_name.replace("source_", "match_")
        items.append((match_col_name, col(f"{mapping.source_name}") == col(f"{mapping.target_name}")))
    return items


def _mismatch_rows_for_aggregate_mappings(
    df: DataFrame,
    select_cols: list[ColumnMapping],
    group_cols: list[ColumnMapping] | None,
) -> DataFrame:
    """Rows where aggregated source/target measures disagree (after optional group-by presence filter)."""
    df_with_match_cols = df
    if group_cols:
        filter_conditions = _agg_conditions(group_cols)
        df_with_match_cols = df_with_match_cols.filter(filter_conditions)
    for match_column_name, match_column in _generate_match_columns(select_cols):
        df_with_match_cols = df_with_match_cols.withColumn(match_column_name, match_column)
    select_conditions = _agg_conditions(select_cols, "select")
    return _filter_to_value_mismatches(
        df_with_match_cols,
        values_equal=select_conditions,
        match_flag_col="agg_data_match",
    )


def reconcile_agg_data_per_rule(
    joined_df: DataFrame,
    source_columns: list[str],
    target_columns: list[str],
    rule: AggregateRule,
) -> DataReconcileOutput:
    """ "
    Generates the reconciliation output for the given rule
    """
    # Generates select columns in the format of:
    # [(source_min_col1, target_min_col1), (source_count_col3, target_count_col3) ... ]

    rule_select_columns = [
        ColumnMapping(
            source_name=f"source_{rule.agg_type}_{rule.agg_column}",
            target_name=f"target_{rule.agg_type}_{rule.agg_column}",
        )
    ]

    rule_group_columns = None
    if rule.group_by_columns:
        rule_group_columns = [
            ColumnMapping(source_name=f"source_group_by_{group_col}", target_name=f"target_group_by_{group_col}")
            for group_col in rule.group_by_columns
        ]
        rule_select_columns.extend(rule_group_columns)

    df_rule_columns = []
    for mapping in rule_select_columns:
        df_rule_columns.extend([mapping.source_name, mapping.target_name])

    joined_df_with_rule_cols = joined_df.select(*df_rule_columns)

    mismatch = _mismatch_rows_for_aggregate_mappings(joined_df_with_rule_cols, rule_select_columns, rule_group_columns)

    # Data missing in Source DataFrame
    rule_target_columns = set(target_columns).intersection([mapping.target_name for mapping in rule_select_columns])

    missing_in_src = joined_df_with_rule_cols.filter(_agg_conditions(rule_select_columns, "missing_in_src")).select(
        *rule_target_columns
    )
    # TODO write `missing_in_tgt` to delta

    # Data missing in Target DataFrame
    rule_source_columns = set(source_columns).intersection([mapping.source_name for mapping in rule_select_columns])
    missing_in_tgt = joined_df_with_rule_cols.filter(_agg_conditions(rule_select_columns, "missing_in_tgt")).select(
        *rule_source_columns
    )
    # TODO write `missing_in_tgt` to delta

    return _data_reconcile_output(
        mismatch_df=mismatch,
        missing_in_src=missing_in_src,
        missing_in_tgt=missing_in_tgt,
    )


# Backward-compatible alias for existing imports/callers
join_aggregate_data = prepare_persisted_aggregate_join
