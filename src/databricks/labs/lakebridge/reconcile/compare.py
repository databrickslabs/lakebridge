import logging
from functools import reduce
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, expr, lit

from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from databricks.labs.lakebridge.reconcile.exception import ColumnMismatchException
from databricks.labs.lakebridge.reconcile.recon_capture import (
    ReconIntermediatePersist,
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


def _build_mismatch_column(table, column):
    return col(DialectUtils.ansi_normalize_identifier(column)).alias(
        DialectUtils.unnormalize_identifier(column.replace(f'{table}_', '').lower())
    )


def reconcile_data(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str],
    report_type: str,
    spark: SparkSession,
    path: str,
) -> DataReconcileOutput:
    source_alias = "src"
    target_alias = "tgt"
    if report_type not in {"data", "all"}:
        key_columns = [_HASH_COLUMN_NAME]
    df = (
        source.alias(source_alias)
        .join(
            other=target.alias(target_alias),
            on=_generate_join_condition(source_alias, target_alias, key_columns),
            how="full",
        )
        .selectExpr(
            *[f'{_build_column_selector(source_alias, col_name)}' for col_name in source.columns],
            *[f'{_build_column_selector(target_alias, col_name)}' for col_name in target.columns],
        )
    )

    # Write unmatched df to volume
    df = ReconIntermediatePersist(spark, path).write_and_read_unmatched_df_with_volumes(df)
    logger.warning(f"Unmatched data was written to {path} successfully")

    mismatch = _get_mismatch_data(df, source_alias, target_alias) if report_type in {"all", "data"} else None

    missing_in_src = (
        df.filter(col(f"{source_alias}_{_HASH_COLUMN_NAME}").isNull())
        .select(
            *[
                _build_mismatch_column(target_alias, col_name)
                for col_name in df.columns
                if col_name.startswith(f'{target_alias}_')
            ]
        )
        .drop(f"{_HASH_COLUMN_NAME}")
    )

    missing_in_tgt = (
        df.filter(col(f"{target_alias}_{_HASH_COLUMN_NAME}").isNull())
        .select(
            *[
                _build_mismatch_column(source_alias, col_name)
                for col_name in df.columns
                if col_name.startswith(f'{source_alias}_')
            ]
        )
        .drop(f"{_HASH_COLUMN_NAME}")
    )
    mismatch_count = 0
    if mismatch:
        mismatch_count = mismatch.count()

    missing_in_src_count = missing_in_src.count()
    missing_in_tgt_count = missing_in_tgt.count()

    return DataReconcileOutput(
        mismatch_count=mismatch_count,
        missing_in_src_count=missing_in_src_count,
        missing_in_tgt_count=missing_in_tgt_count,
        missing_in_src=missing_in_src.limit(_SAMPLE_ROWS),
        missing_in_tgt=missing_in_tgt.limit(_SAMPLE_ROWS),
        mismatch=MismatchOutput(mismatch_df=mismatch),
    )


def _get_mismatch_data(df: DataFrame, src_alias: str, tgt_alias: str) -> DataFrame:
    return (
        df.filter(
            (col(f"{src_alias}_{_HASH_COLUMN_NAME}").isNotNull())
            & (col(f"{tgt_alias}_{_HASH_COLUMN_NAME}").isNotNull())
        )
        .withColumn(
            "hash_match",
            col(f"{src_alias}_{_HASH_COLUMN_NAME}") == col(f"{tgt_alias}_{_HASH_COLUMN_NAME}"),
        )
        .filter(col("hash_match") == lit(False))
        .select(
            *[
                _build_mismatch_column(src_alias, col_name)
                for col_name in df.columns
                if col_name.startswith(f'{src_alias}_')
            ]
        )
        .drop(f"{_HASH_COLUMN_NAME}")
    )


def _build_capture_df(df: DataFrame) -> DataFrame:
    columns = [
        col(DialectUtils.ansi_normalize_identifier(column)).alias(DialectUtils.unnormalize_identifier(column))
        for column in df.columns
    ]
    return df.select(*columns)


def capture_mismatch_data_and_columns(source: DataFrame, target: DataFrame, key_columns: list[str]) -> MismatchOutput:
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


def _get_mismatch_df(source: DataFrame, target: DataFrame, key_columns: list[str], column_list: list[str]):
    source_aliased = [
        col('base.' + DialectUtils.ansi_normalize_identifier(column)).alias(
            _unnormalize_mismatch_df_col(column, '_base')
        )
        for column in column_list
    ]
    target_aliased = [
        col('compare.' + DialectUtils.ansi_normalize_identifier(column)).alias(
            _unnormalize_mismatch_df_col(column, '_compare')
        )
        for column in column_list
    ]

    match_expr = [
        expr(f"{_normalize_mismatch_df_col(column,'_base')}=={_normalize_mismatch_df_col(column,'_compare')}").alias(
            _unnormalize_mismatch_df_col(column, '_match')
        )
        for column in column_list
    ]
    key_cols = [col(DialectUtils.ansi_normalize_identifier(column)) for column in key_columns]
    select_expr = key_cols + source_aliased + target_aliased + match_expr

    logger.info(f"KEY COLUMNS: {key_columns}")
    logger.info(f"SELECT COLUMNS: {select_expr}")

    mismatch_df = (
        source.alias('base').join(other=target.alias('compare'), on=key_columns, how="inner").select(*select_expr)
    )

    compare_columns = [
        DialectUtils.ansi_normalize_identifier(column) for column in mismatch_df.columns if column not in key_columns
    ]
    return mismatch_df.select(*key_cols + sorted(compare_columns))


def _generate_agg_join_condition(source_alias: str, target_alias: str, key_columns: list[str]):
    join_columns: list[ColumnMapping] = [
        ColumnMapping(source_name=f"source_group_by_{key_col}", target_name=f"target_group_by_{key_col}")
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
            (col(f"{mapping.source_name}").isNotNull() & col(f"{mapping.target_name}").isNotNull()) for mapping in cols
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


def _get_mismatch_agg_data(
    df: DataFrame,
    select_cols: list[ColumnMapping],
    group_cols: list[ColumnMapping] | None,
) -> DataFrame:
    # TODO:  Integrate with _get_mismatch_data function
    """
    For each rule select columns, generate a match column to compare the aggregated data between Source and Target

      e.g., select_cols = [(source_min_col1, target_min_col1), (source_count_col3, target_count_col3)]

            source_min_col1 | target_min_col1 | match_min_col1 |  agg_data_match |
            -----------------|--------------------|----------------|-------------------|
                   11    |   12    |source_min_col1 == target_min_col1 | False                     |

    :param df: Joined DataFrame with aggregated data from Source and Target
    :param select_cols:  Rule specific select columns
    :param group_cols: Rule specific group by columns, if any
    :return: DataFrame with match_<AGG_TYPE>_<COLUMN> and agg_data_match columns
                 to identify the aggregate data mismatch between Source and Target
    """
    df_with_match_cols = df

    if group_cols:
        # Filter Conditions are in the format of: source_group_by_col1 is not null and target_group_by_col1 is not null
        filter_conditions = _agg_conditions(group_cols)
        df_with_match_cols = df_with_match_cols.filter(filter_conditions)

    # Generate match columns for the select columns. e.g., match_<AGG_TYPE>_<COLUMN>
    for match_column_name, match_column in _generate_match_columns(select_cols):
        df_with_match_cols = df_with_match_cols.withColumn(match_column_name, match_column)

    # e.g., source_min_col1 == target_min_col1 and source_count_col3 == target_count_col3
    select_conditions = _agg_conditions(select_cols, "select")

    return df_with_match_cols.withColumn("agg_data_match", select_conditions).filter(
        col("agg_data_match") == lit(False)
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

    joined_df_with_rule_cols = joined_df.selectExpr(*df_rule_columns)

    # Data mismatch between Source and Target aggregated data
    mismatch = _get_mismatch_agg_data(joined_df_with_rule_cols, rule_select_columns, rule_group_columns)

    # Data missing in Source DataFrame
    rule_target_columns = set(target_columns).intersection([mapping.target_name for mapping in rule_select_columns])

    missing_in_src = joined_df_with_rule_cols.filter(_agg_conditions(rule_select_columns, "missing_in_src")).select(
        *rule_target_columns
    )

    # Data missing in Target DataFrame
    rule_source_columns = set(source_columns).intersection([mapping.source_name for mapping in rule_select_columns])
    missing_in_tgt = joined_df_with_rule_cols.filter(_agg_conditions(rule_select_columns, "missing_in_tgt")).select(
        *rule_source_columns
    )

    mismatch_count = 0
    if mismatch:
        mismatch_count = mismatch.count()

    rule_reconcile_output = DataReconcileOutput(
        mismatch_count=mismatch_count,
        missing_in_src_count=missing_in_src.count(),
        missing_in_tgt_count=missing_in_tgt.count(),
        missing_in_src=missing_in_src.limit(_SAMPLE_ROWS),
        missing_in_tgt=missing_in_tgt.limit(_SAMPLE_ROWS),
        mismatch=MismatchOutput(mismatch_df=mismatch),
    )

    return rule_reconcile_output


def join_aggregate_data(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str] | None,
    spark: SparkSession,
    path: str,
) -> DataFrame:
    # TODO:  Integrate with reconcile_data function

    source_alias = "src"
    target_alias = "tgt"

    # Generates group by columns in the format of:
    # [(source_group_by_col1, target_group_by_col1), (source_group_by_col2, target_group_by_col2) ... ]

    if key_columns:
        # If there are Group By columns, do Full join on the grouped columns
        df = source.alias(source_alias).join(
            other=target.alias(target_alias),
            on=_generate_agg_join_condition(source_alias, target_alias, key_columns),
            how="full",
        )
    else:
        # If there is no Group By condition, do Cross join as there is only one record
        df = source.alias(source_alias).join(
            other=target.alias(target_alias),
            how="cross",
        )

    joined_df = df.selectExpr(
        *source.columns,
        *target.columns,
    )

    # Write the joined df to volume path
    joined_volume_df = ReconIntermediatePersist(spark, path).write_and_read_unmatched_df_with_volumes(joined_df).cache()
    logger.warning(f"Unmatched data is written to {path} successfully")

    return joined_volume_df
