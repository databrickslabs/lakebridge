import re

import pytest

from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.exception import InvalidInputException
from databricks.labs.lakebridge.reconcile.query_builder.threshold_query import (
    ThresholdQueryBuilder,
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    JdbcReaderOptions,
    Schema,
    ColumnThresholds,
    Transformation,
)


def test_threshold_comparison_query_with_one_threshold(table_conf_with_opts, table_schema):
    # table conf
    table_conf = table_conf_with_opts
    # schema
    table_schema, _ = table_schema
    table_schema.append(Schema("s_suppdate", "timestamp"))
    comparison_query = ThresholdQueryBuilder(
        table_conf, table_schema, "source", get_dialect("oracle")
    ).build_comparison_query()
    assert re.sub(r'\s+', ' ', comparison_query.strip().lower()) == re.sub(
        r'\s+',
        ' ',
        """select coalesce(source.s_acctbal, 0) as s_acctbal_source, coalesce(databricks.s_acctbal,
        0) as s_acctbal_databricks, case when (coalesce(source.s_acctbal, 0) - coalesce(databricks.s_acctbal,
        0)) = 0 then 'match' when (coalesce(source.s_acctbal, 0) - coalesce(databricks.s_acctbal, 0)) between 0 and
        100 then 'warning' else 'failed' end as s_acctbal_match, source.s_nationkey as s_nationkey_source,
        source.s_suppkey as s_suppkey_source from source_supplier_df_threshold_vw as source inner join
        target_target_supplier_df_threshold_vw as databricks on source.s_nationkey <=> databricks.s_nationkey and
        source.s_suppkey <=> databricks.s_suppkey where (1 = 1 or 1 = 1) or
        (coalesce(source.s_acctbal, 0) - coalesce(databricks.s_acctbal, 0)) <> 0""".strip().lower(),
    )


def test_threshold_comparison_query_with_dual_threshold(table_conf_with_opts, table_schema):
    # table conf
    table_conf = table_conf_with_opts
    table_conf.join_columns = ["s_suppkey", "s_suppdate"]
    table_conf.column_thresholds = [
        ColumnThresholds(column_name="s_acctbal", lower_bound="5%", upper_bound="-5%", type="float"),
        ColumnThresholds(column_name="s_suppdate", lower_bound="-86400", upper_bound="86400", type="timestamp"),
    ]

    # schema
    table_schema, _ = table_schema
    table_schema.append(Schema("s_suppdate", "timestamp"))

    comparison_query = ThresholdQueryBuilder(
        table_conf, table_schema, "target", get_dialect("databricks")
    ).build_comparison_query()
    assert re.sub(r'\s+', ' ', comparison_query.strip().lower()) == re.sub(
        r'\s+',
        ' ',
        """select coalesce(source.s_acctbal, 0) as s_acctbal_source, coalesce(databricks.s_acctbal,
        0) as s_acctbal_databricks, case when (coalesce(source.s_acctbal, 0) - coalesce(databricks.s_acctbal,
        0)) = 0 then 'match' when ((coalesce(source.s_acctbal, 0) - coalesce(databricks.s_acctbal,
        0)) / if(databricks.s_acctbal = 0 or databricks.s_acctbal is null, 1, databricks.s_acctbal)) * 100 between 5
        and -5 then 'warning' else 'failed' end as s_acctbal_match, coalesce(unix_timestamp(source.s_suppdate),
        0) as s_suppdate_source, coalesce(unix_timestamp(databricks.s_suppdate), 0) as s_suppdate_databricks,
        case when (coalesce(unix_timestamp(source.s_suppdate), 0) - coalesce(unix_timestamp(databricks.s_suppdate),
        0)) = 0 then 'match' when (coalesce(unix_timestamp(source.s_suppdate), 0) -
        coalesce(unix_timestamp(databricks.s_suppdate), 0)) between -86400 and 86400 then
        'warning' else 'failed' end as s_suppdate_match, source.s_suppdate as s_suppdate_source,
        source.s_suppkey as s_suppkey_source from source_supplier_df_threshold_vw as
        source inner join target_target_supplier_df_threshold_vw as databricks on source.s_suppdate <=> databricks.s_suppdate and
        source.s_suppkey <=> databricks.s_suppkey where (1 = 1 or 1 = 1) or (coalesce(source.s_acctbal, 0) -
        coalesce(databricks.s_acctbal, 0)) <> 0 or (coalesce(unix_timestamp(source.s_suppdate), 0) -
        coalesce(unix_timestamp(databricks.s_suppdate), 0)) <> 0""".strip().lower(),
    )


def test_build_threshold_query_with_single_threshold(table_conf_with_opts, table_schema):
    table_conf = table_conf_with_opts
    table_conf.jdbc_reader_options = None
    table_conf.transformations = [
        Transformation(column_name="s_acctbal", source="cast(s_acctbal as number)", target="cast(s_acctbal_t as int)")
    ]
    src_schema, tgt_schema = table_schema
    src_query = ThresholdQueryBuilder(table_conf, src_schema, "source", get_dialect("oracle")).build_threshold_query()
    target_query = ThresholdQueryBuilder(
        table_conf, tgt_schema, "target", get_dialect("databricks")
    ).build_threshold_query()
    assert src_query == (
        "SELECT s_nationkey AS s_nationkey, s_suppkey AS s_suppkey, "
        "CAST(s_acctbal AS NUMBER) AS s_acctbal FROM :tbl WHERE s_name = 't' AND s_address = 'a'"
    )
    assert target_query == (
        "SELECT s_nationkey_t AS s_nationkey, s_suppkey_t AS s_suppkey, "
        "CAST(s_acctbal_t AS INT) AS s_acctbal FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'"
    )


def test_build_threshold_query_with_multiple_threshold(table_conf_with_opts, table_schema):
    table_conf = table_conf_with_opts
    table_conf.jdbc_reader_options = JdbcReaderOptions(
        number_partitions=100, partition_column="s_phone", lower_bound="0", upper_bound="100"
    )
    table_conf.column_thresholds = [
        ColumnThresholds(column_name="s_acctbal", lower_bound="5%", upper_bound="-5%", type="float"),
        ColumnThresholds(column_name="s_suppdate", lower_bound="-86400", upper_bound="86400", type="timestamp"),
    ]
    table_conf.filters = None
    src_schema, tgt_schema = table_schema
    src_schema.append(Schema("s_suppdate", "timestamp"))
    tgt_schema.append(Schema("s_suppdate", "timestamp"))
    src_query = ThresholdQueryBuilder(table_conf, src_schema, "source", get_dialect("oracle")).build_threshold_query()
    target_query = ThresholdQueryBuilder(
        table_conf, tgt_schema, "target", get_dialect("databricks")
    ).build_threshold_query()
    assert src_query == (
        "SELECT s_nationkey AS s_nationkey, TRIM(s_phone) AS s_phone, s_suppkey "
        "AS s_suppkey, s_acctbal AS s_acctbal, s_suppdate AS s_suppdate FROM :tbl"
    )
    assert target_query == (
        "SELECT s_nationkey_t AS s_nationkey, s_suppkey_t AS s_suppkey, "
        "s_acctbal_t AS s_acctbal, s_suppdate AS s_suppdate FROM :tbl"
    )


def test_build_expression_type_raises_value_error(table_conf_with_opts, table_schema):
    table_conf = table_conf_with_opts
    table_conf.column_thresholds = [
        ColumnThresholds(column_name="s_acctbal", lower_bound="5%", upper_bound="-5%", type="unknown"),
    ]
    table_conf.filters = None
    src_schema, tgt_schema = table_schema
    src_schema.append(Schema("s_suppdate", "timestamp"))
    tgt_schema.append(Schema("s_suppdate", "timestamp"))

    with pytest.raises(ValueError):
        ThresholdQueryBuilder(table_conf, src_schema, "source", get_dialect("oracle")).build_comparison_query()


def test_test_no_join_columns_raise_exception(table_conf_with_opts, table_schema):
    table_conf = table_conf_with_opts
    table_conf.join_columns = None
    src_schema, tgt_schema = table_schema
    src_schema.append(Schema("s_suppdate", "timestamp"))
    tgt_schema.append(Schema("s_suppdate", "timestamp"))

    with pytest.raises(InvalidInputException):
        ThresholdQueryBuilder(table_conf, src_schema, "source", get_dialect("oracle")).build_comparison_query()
