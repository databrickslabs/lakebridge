import sys

from dataclasses import dataclass
from pathlib import Path

from unittest.mock import patch

import pytest
from pyspark.testing import assertDataFrameEqual
from pyspark.sql import Row

from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.execute import Reconciliation, main
from databricks.labs.lakebridge.reconcile.recon_config import (
    Aggregate,
    AggregateRule,
)
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    AggregateQueryOutput,
    DataReconcileOutput,
    MismatchOutput,
)
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare

CATALOG = "org"
SCHEMA = "data"
SRC_TABLE = "supplier"
TGT_TABLE = "target_supplier"


@dataclass
class AggregateQueries:
    source_agg_query: str
    target_agg_query: str
    source_group_agg_query: str
    target_group_agg_query: str


@dataclass
class AggregateQueryStore:
    agg_queries: AggregateQueries


@pytest.fixture
def query_store(mock_spark):
    agg_queries = AggregateQueries(
        source_agg_query="SELECT min(s_acctbal) AS source_min_s_acctbal FROM :tbl WHERE s_name = 't' AND s_address = 'a'",
        target_agg_query="SELECT min(s_acctbal_t) AS target_min_s_acctbal FROM :tbl WHERE s_name = 't' AND s_address_t = 'a'",
        source_group_agg_query="SELECT sum(s_acctbal) AS source_sum_s_acctbal, count(TRIM(s_name)) AS source_count_s_name, s_nationkey AS source_group_by_s_nationkey FROM :tbl WHERE s_name = 't' AND s_address = 'a' GROUP BY s_nationkey",
        target_group_agg_query="SELECT sum(s_acctbal_t) AS target_sum_s_acctbal, count(TRIM(s_name)) AS target_count_s_name, s_nationkey_t AS target_group_by_s_nationkey FROM :tbl WHERE s_name = 't' AND s_address_t = 'a' GROUP BY s_nationkey_t",
    )

    return AggregateQueryStore(
        agg_queries=agg_queries,
    )


def test_reconcile_aggregate_data_missing_records(
    mock_spark,
    table_conf_with_opts,
    table_schema,
    query_store,
    tmp_path: Path,
):
    src_schema, tgt_schema = table_schema
    table_conf_with_opts.drop_columns = ["s_acctbal"]
    table_conf_with_opts.column_thresholds = None
    table_conf_with_opts.aggregates = [Aggregate(type="MIN", agg_columns=["s_acctbal"])]

    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.agg_queries.source_agg_query,
        ): mock_spark.createDataFrame(
            [
                Row(source_min_s_acctbal=11),
            ]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.agg_queries.target_agg_query,
        ): mock_spark.createDataFrame(
            [
                Row(target_min_s_acctbal=10),
            ]
        )
    }

    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    database_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    target = MockDataSource(target_dataframe_repository, target_schema_repository)
    with patch("databricks.labs.lakebridge.reconcile.execute.generate_volume_path", return_value=str(tmp_path)):
        actual: list[AggregateQueryOutput] = Reconciliation(
            source,
            target,
            database_config,
            "",
            SchemaCompare(mock_spark),
            get_dialect("databricks"),
            mock_spark,
            ReconcileMetadataConfig(),
        ).reconcile_aggregates(table_conf_with_opts, src_schema, tgt_schema)

        assert len(actual) == 1

        assert actual[0].rule, "Rule must be generated"

        assert actual[0].rule.agg_type == "min"
        assert actual[0].rule.agg_column == "s_acctbal"
        assert actual[0].rule.group_by_columns is None
        assert actual[0].rule.group_by_columns_as_str == "NA"
        assert actual[0].rule.group_by_columns_as_table_column == "NULL"
        assert actual[0].rule.column_from_rule == "min_s_acctbal_NA"
        assert actual[0].rule.rule_type == "AGGREGATE"

        assert actual[0].reconcile_output.mismatch.mismatch_df, "Mismatch dataframe must be present"
        assert not actual[0].reconcile_output.mismatch.mismatch_df.isEmpty()

        expected = DataReconcileOutput(
            mismatch_count=1,
            missing_in_src_count=0,
            missing_in_tgt_count=0,
            mismatch=MismatchOutput(
                mismatch_columns=None,
                mismatch_df=mock_spark.createDataFrame(
                    [
                        Row(
                            source_min_s_acctbal=11,
                            target_min_s_acctbal=10,
                            match_min_s_acctbal=False,
                            agg_data_match=False,
                        )
                    ]
                ),
            ),
        )

        assert actual[0].reconcile_output.mismatch_count == expected.mismatch_count
        assert actual[0].reconcile_output.missing_in_src_count == expected.missing_in_src_count
        assert actual[0].reconcile_output.missing_in_tgt_count == expected.missing_in_tgt_count
        assertDataFrameEqual(actual[0].reconcile_output.mismatch.mismatch_df, expected.mismatch.mismatch_df)


def expected_rule_output():
    count_rule_output = AggregateRule(
        agg_type="count",
        agg_column="s_name",
        group_by_columns=["s_nationkey"],
        group_by_columns_as_str="s_nationkey",
    )

    sum_rule_output = AggregateRule(
        agg_type="sum",
        agg_column="s_acctbal",
        group_by_columns=["s_nationkey"],
        group_by_columns_as_str="s_nationkey",
    )

    return {"count": count_rule_output, "sum": sum_rule_output}


def expected_reconcile_output_dict(spark):
    count_reconcile_output = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
        mismatch=MismatchOutput(
            mismatch_columns=None,
            mismatch_df=spark.createDataFrame(
                [
                    Row(
                        source_count_s_name=11,
                        target_count_s_name=9,
                        source_group_by_s_nationkey=12,
                        target_group_by_s_nationkey=12,
                        match_count_s_name=False,
                        match_group_by_s_nationkey=True,
                        agg_data_match=False,
                    )
                ]
            ),
        ),
        missing_in_src=spark.createDataFrame([Row(target_count_s_name=76, target_group_by_s_nationkey=14)]),
        missing_in_tgt=spark.createDataFrame([Row(source_count_s_name=21, source_group_by_s_nationkey=13)]),
    )

    sum_reconcile_output = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
        mismatch=MismatchOutput(
            mismatch_columns=None,
            mismatch_df=spark.createDataFrame(
                [
                    Row(
                        source_sum_s_acctbal=23,
                        target_sum_s_acctbal=43,
                        source_group_by_s_nationkey=12,
                        target_group_by_s_nationkey=12,
                        match_sum_s_acctbal=False,
                        match_group_by_s_nationkey=True,
                        agg_data_match=False,
                    )
                ]
            ),
        ),
        missing_in_src=spark.createDataFrame([Row(target_sum_s_acctbal=348, target_group_by_s_nationkey=14)]),
        missing_in_tgt=spark.createDataFrame([Row(source_sum_s_acctbal=112, source_group_by_s_nationkey=13)]),
    )

    return {"count": count_reconcile_output, "sum": sum_reconcile_output}


def _compare_reconcile_output(actual_reconcile_output: DataReconcileOutput, expected_reconcile: DataReconcileOutput):
    # Reconcile Output validations
    if actual_reconcile_output and expected_reconcile:
        assert actual_reconcile_output.mismatch.mismatch_df, "Mismatch dataframe must be present"
        assert actual_reconcile_output.missing_in_src, "Missing in source one record must be present"
        assert actual_reconcile_output.missing_in_tgt, "Missing in target one record must be present"

        assert actual_reconcile_output.mismatch_count == expected_reconcile.mismatch_count
        assert actual_reconcile_output.missing_in_src_count == expected_reconcile.missing_in_src_count
        assert actual_reconcile_output.missing_in_tgt_count == expected_reconcile.missing_in_tgt_count

        if actual_reconcile_output.mismatch.mismatch_df and expected_reconcile.mismatch.mismatch_df:
            mismatch_df_columns = actual_reconcile_output.mismatch.mismatch_df.columns
            assertDataFrameEqual(
                actual_reconcile_output.mismatch.mismatch_df.select(*mismatch_df_columns),
                expected_reconcile.mismatch.mismatch_df.select(*mismatch_df_columns),
            )

        if actual_reconcile_output.missing_in_src and expected_reconcile.missing_in_src:
            missing_in_src_columns = actual_reconcile_output.missing_in_src.columns
            assertDataFrameEqual(
                actual_reconcile_output.missing_in_src.select(*missing_in_src_columns),
                expected_reconcile.missing_in_src.select(*missing_in_src_columns),
            )

        if actual_reconcile_output.missing_in_tgt and expected_reconcile.missing_in_tgt:
            missing_in_tgt_columns = actual_reconcile_output.missing_in_tgt.columns
            assert (
                actual_reconcile_output.missing_in_tgt.select(*missing_in_tgt_columns).first()
                == expected_reconcile.missing_in_tgt.select(*missing_in_tgt_columns).first()
            )


def test_reconcile_aggregate_data_mismatch_and_missing_records(
    mock_spark,
    table_conf_with_opts,
    table_schema,
    query_store,
    tmp_path: Path,
):
    src_schema, tgt_schema = table_schema
    table_conf_with_opts.drop_columns = ["s_acctbal"]
    table_conf_with_opts.column_thresholds = None
    table_conf_with_opts.aggregates = [
        Aggregate(type="SUM", agg_columns=["s_acctbal"], group_by_columns=["s_nationkey"]),
        Aggregate(type="COUNT", agg_columns=["s_name"], group_by_columns=["s_nationkey"]),
    ]

    source_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.agg_queries.source_group_agg_query,
        ): mock_spark.createDataFrame(
            [
                Row(source_sum_s_acctbal=101, source_count_s_name=13, source_group_by_s_nationkey=11),
                Row(source_sum_s_acctbal=23, source_count_s_name=11, source_group_by_s_nationkey=12),
                Row(source_sum_s_acctbal=112, source_count_s_name=21, source_group_by_s_nationkey=13),
            ]
        ),
    }
    source_schema_repository = {(CATALOG, SCHEMA, SRC_TABLE): src_schema}

    target_dataframe_repository = {
        (
            CATALOG,
            SCHEMA,
            query_store.agg_queries.target_group_agg_query,
        ): mock_spark.createDataFrame(
            [
                Row(target_sum_s_acctbal=101, target_count_s_name=13, target_group_by_s_nationkey=11),
                Row(target_sum_s_acctbal=43, target_count_s_name=9, target_group_by_s_nationkey=12),
                Row(target_sum_s_acctbal=348, target_count_s_name=76, target_group_by_s_nationkey=14),
            ]
        )
    }

    target_schema_repository = {(CATALOG, SCHEMA, TGT_TABLE): tgt_schema}
    db_config = DatabaseConfig(
        source_catalog=CATALOG,
        source_schema=SCHEMA,
        target_catalog=CATALOG,
        target_schema=SCHEMA,
    )
    source = MockDataSource(source_dataframe_repository, source_schema_repository)
    with patch("databricks.labs.lakebridge.reconcile.execute.generate_volume_path", return_value=str(tmp_path)):
        actual_list: list[AggregateQueryOutput] = Reconciliation(
            source,
            MockDataSource(target_dataframe_repository, target_schema_repository),
            db_config,
            "",
            SchemaCompare(mock_spark),
            get_dialect("databricks"),
            mock_spark,
            ReconcileMetadataConfig(),
        ).reconcile_aggregates(table_conf_with_opts, src_schema, tgt_schema)

        assert len(actual_list) == 2

        for actual in actual_list:
            assert actual.rule, "Rule must be generated"
            expected_rule = expected_rule_output().get(actual.rule.agg_type)
            assert expected_rule, "Rule must be defined in expected"

            # Rule validations
            assert actual.rule.agg_type == expected_rule.agg_type
            assert actual.rule.agg_column == expected_rule.agg_column
            assert actual.rule.group_by_columns == expected_rule.group_by_columns
            assert actual.rule.group_by_columns_as_str == expected_rule.group_by_columns_as_str
            assert actual.rule.group_by_columns_as_table_column == expected_rule.group_by_columns_as_table_column
            assert (
                actual.rule.column_from_rule
                == f"{expected_rule.agg_type}_{expected_rule.agg_column}_{expected_rule.group_by_columns_as_str}"
            )
            assert actual.rule.rule_type == "AGGREGATE"

            # Reconcile Output validations
            _compare_reconcile_output(
                actual.reconcile_output, expected_reconcile_output_dict(mock_spark).get(actual.rule.agg_type)
            )


def test_run_with_invalid_operation_name(monkeypatch):
    test_args = ["databricks_labs_remorph", "invalid-operation"]
    monkeypatch.setattr(sys, 'argv', test_args)
    with pytest.raises(AssertionError, match="Invalid option:"):
        main()


def test_aggregates_reconcile_invalid_aggregates():
    invalid_agg_type_message = "Invalid aggregate type: std, only .* are supported."
    with pytest.raises(AssertionError, match=invalid_agg_type_message):
        Aggregate(agg_columns=["discount"], group_by_columns=["p_id"], type="STD")


def test_aggregates_reconcile_aggregate_columns():
    agg = Aggregate(agg_columns=["discount", "price"], group_by_columns=["p_dept_id", "p_sub_dept"], type="STDDEV")

    assert agg.get_agg_type() == "stddev"
    assert agg.group_by_columns_as_str == "p_dept_id+__+p_sub_dept"
    assert agg.agg_columns_as_str == "discount+__+price"

    agg1 = Aggregate(agg_columns=["discount"], type="MAX")
    assert agg1.get_agg_type() == "max"
    assert agg1.group_by_columns_as_str == "NA"
    assert agg1.agg_columns_as_str == "discount"


def test_aggregates_reconcile_aggregate_rule():
    agg_rule = AggregateRule(
        agg_column="discount",
        group_by_columns=["p_dept_id", "p_sub_dept"],
        group_by_columns_as_str="p_dept_id+__+p_sub_dept",
        agg_type="stddev",
    )

    assert agg_rule.column_from_rule == "stddev_discount_p_dept_id+__+p_sub_dept"
    assert agg_rule.group_by_columns_as_table_column == "\"p_dept_id, p_sub_dept\""
    expected_rule_query = """ SELECT 1234 as rule_id,  'AGGREGATE' as rule_type,   map( 'agg_type', 'stddev',
                 'agg_column', 'discount',
                 'group_by_columns', "p_dept_id, p_sub_dept"
                 )
         as rule_info """
    assert agg_rule.get_rule_query(1234) == expected_rule_query


agg_rule1 = AggregateRule(agg_column="discount", group_by_columns=None, group_by_columns_as_str="NA", agg_type="max")
assert agg_rule1.column_from_rule == "max_discount_NA"
assert agg_rule1.group_by_columns_as_table_column == "NULL"
EXPECTED_RULE1_QUERY = """ SELECT 1234 as rule_id,  'AGGREGATE' as rule_type,   map( 'agg_type', 'max',
                 'agg_column', 'discount',
                 'group_by_columns', NULL
                 )
         as rule_info """
assert agg_rule1.get_rule_query(1234) == EXPECTED_RULE1_QUERY
