from pathlib import Path
import pytest
from pyspark import Row
from pyspark.testing import assertDataFrameEqual

from databricks.labs.lakebridge.reconcile.compare import (
    alias_column_str,
    capture_mismatch_data_and_columns,
    reconcile_data,
)
from databricks.labs.lakebridge.reconcile.exception import ColumnMismatchException
from databricks.labs.lakebridge.reconcile.recon_output_config import (
    DataReconcileOutput,
    MismatchOutput,
)


def test_compare_data_for_report_all(
    mock_spark,
    tmp_path: Path,
):
    source = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, hash_value_recon='1a1'),
            Row(s_suppkey=2, s_nationkey=22, hash_value_recon='2b2'),
            Row(s_suppkey=3, s_nationkey=33, hash_value_recon='3c3'),
            Row(s_suppkey=5, s_nationkey=55, hash_value_recon='5e5'),
        ]
    )
    target = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, hash_value_recon='1a1'),
            Row(s_suppkey=2, s_nationkey=22, hash_value_recon='2b4'),
            Row(s_suppkey=4, s_nationkey=44, hash_value_recon='4d4'),
            Row(s_suppkey=5, s_nationkey=56, hash_value_recon='5e6'),
        ]
    )

    mismatch = MismatchOutput(mismatch_df=mock_spark.createDataFrame([Row(s_suppkey=2, s_nationkey=22)]))
    missing_in_src = mock_spark.createDataFrame([Row(s_suppkey=4, s_nationkey=44), Row(s_suppkey=5, s_nationkey=56)])
    missing_in_tgt = mock_spark.createDataFrame([Row(s_suppkey=3, s_nationkey=33), Row(s_suppkey=5, s_nationkey=55)])

    actual = reconcile_data(
        source=source,
        target=target,
        key_columns=["s_suppkey", "s_nationkey"],
        report_type="all",
        spark=mock_spark,
        path=str(tmp_path),
    )
    expected = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
        missing_in_src=missing_in_src,
        missing_in_tgt=missing_in_tgt,
        mismatch=mismatch,
    )

    assertDataFrameEqual(actual.mismatch.mismatch_df, expected.mismatch.mismatch_df)
    assertDataFrameEqual(actual.missing_in_src, expected.missing_in_src)
    assertDataFrameEqual(actual.missing_in_tgt, expected.missing_in_tgt)


def test_compare_data_for_report_hash(mock_spark, tmp_path: Path):
    source = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, hash_value_recon='1a1'),
            Row(s_suppkey=2, s_nationkey=22, hash_value_recon='2b2'),
            Row(s_suppkey=3, s_nationkey=33, hash_value_recon='3c3'),
            Row(s_suppkey=5, s_nationkey=55, hash_value_recon='5e5'),
        ]
    )
    target = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, hash_value_recon='1a1'),
            Row(s_suppkey=2, s_nationkey=22, hash_value_recon='2b4'),
            Row(s_suppkey=4, s_nationkey=44, hash_value_recon='4d4'),
            Row(s_suppkey=5, s_nationkey=56, hash_value_recon='5e6'),
        ]
    )

    missing_in_src = mock_spark.createDataFrame(
        [Row(s_suppkey=2, s_nationkey=22), Row(s_suppkey=4, s_nationkey=44), Row(s_suppkey=5, s_nationkey=56)]
    )
    missing_in_tgt = mock_spark.createDataFrame(
        [Row(s_suppkey=2, s_nationkey=22), Row(s_suppkey=3, s_nationkey=33), Row(s_suppkey=5, s_nationkey=55)]
    )

    actual = reconcile_data(
        source=source,
        target=target,
        key_columns=["s_suppkey", "s_nationkey"],
        report_type="hash",
        spark=mock_spark,
        path=str(tmp_path),
    )
    expected = DataReconcileOutput(
        missing_in_src=missing_in_src,
        missing_in_tgt=missing_in_tgt,
        mismatch=MismatchOutput(),
        mismatch_count=0,
        missing_in_src_count=1,
        missing_in_tgt_count=1,
    )

    assert actual.mismatch.mismatch_df is None
    assert not actual.mismatch.mismatch_columns
    assertDataFrameEqual(actual.missing_in_src, expected.missing_in_src)
    assertDataFrameEqual(actual.missing_in_tgt, expected.missing_in_tgt)


def test_capture_mismatch_data_and_cols(mock_spark):
    # these mock dataframes are expected to contain only mismatched rows. Hence, the matching rows between source and target are removed for this test-case.
    source = mock_spark.createDataFrame(
        [
            Row(s_suppkey=2, s_nationkey=22, s_name='supp-22', s_address='a-2', s_phone='ph-2', s_acctbal=200),
            Row(s_suppkey=3, s_nationkey=33, s_name='supp-3', s_address='a-3', s_phone='ph-3', s_acctbal=300),
            Row(s_suppkey=5, s_nationkey=55, s_name='supp-5', s_address='a-5', s_phone='ph-5', s_acctbal=400),
        ]
    )
    target = mock_spark.createDataFrame(
        [
            Row(s_suppkey=2, s_nationkey=22, s_name='supp-2', s_address='a-2', s_phone='ph-2', s_acctbal=2000),
            Row(s_suppkey=3, s_nationkey=33, s_name='supp-33', s_address='a-3', s_phone='ph-3', s_acctbal=300),
            Row(s_suppkey=4, s_nationkey=44, s_name='supp-4', s_address='a-4', s_phone='ph-4', s_acctbal=400),
        ]
    )

    actual = capture_mismatch_data_and_columns(source=source, target=target, key_columns=["s_suppkey", "s_nationkey"])

    expected_df = mock_spark.createDataFrame(
        [
            Row(
                s_suppkey=2,
                s_nationkey=22,
                s_acctbal_base=200,
                s_acctbal_compare=2000,
                s_acctbal_match=False,
                s_address_base='a-2',
                s_address_compare='a-2',
                s_address_match=True,
                s_name_base='supp-22',
                s_name_compare='supp-2',
                s_name_match=False,
                s_phone_base='ph-2',
                s_phone_compare='ph-2',
                s_phone_match=True,
            ),
            Row(
                s_suppkey=3,
                s_nationkey=33,
                s_acctbal_base=300,
                s_acctbal_compare=300,
                s_acctbal_match=True,
                s_address_base='a-3',
                s_address_compare='a-3',
                s_address_match=True,
                s_name_base='supp-3',
                s_name_compare='supp-33',
                s_name_match=False,
                s_phone_base='ph-3',
                s_phone_compare='ph-3',
                s_phone_match=True,
            ),
        ]
    )

    assertDataFrameEqual(actual.mismatch_df, expected_df)
    assert sorted(actual.mismatch_columns) == ['s_acctbal', 's_name']


def test_capture_mismatch_data_and_cols_no_mismatch(mock_spark):
    # this is to test the behaviour of the function `capture_mismatch_data_and_columns` when there is no mismatch in the dataframes.
    source = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, s_name='supp-1', s_address='a-1', s_phone='ph-1', s_acctbal=100),
        ]
    )

    target = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, s_name='supp-1', s_address='a-1', s_phone='ph-1', s_acctbal=100),
        ]
    )

    actual = capture_mismatch_data_and_columns(source=source, target=target, key_columns=["s_suppkey", "s_nationkey"])

    expected_df = mock_spark.createDataFrame(
        [
            Row(
                s_suppkey=1,
                s_nationkey=11,
                s_acctbal_base=100,
                s_acctbal_compare=100,
                s_acctbal_match=True,
                s_address_base='a-1',
                s_address_compare='a-1',
                s_address_match=True,
                s_name_base='supp-1',
                s_name_compare='supp-1',
                s_name_match=True,
                s_phone_base='ph-1',
                s_phone_compare='ph-1',
                s_phone_match=True,
            ),
        ]
    )

    assertDataFrameEqual(actual.mismatch_df, expected_df)
    assert sorted(actual.mismatch_columns) == []


def test_capture_mismatch_data_and_cols_fail(mock_spark):
    source = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1, s_nationkey=11, s_name='supp-1', s_address='a-1', s_phone='ph-1', s_acctbal=100),
            Row(s_suppkey=2, s_nationkey=22, s_name='supp-22', s_address='a-2', s_phone='ph-2', s_acctbal=200),
            Row(s_suppkey=3, s_nationkey=33, s_name='supp-3', s_address='a-3', s_phone='ph-3', s_acctbal=300),
            Row(s_suppkey=5, s_nationkey=55, s_name='supp-5', s_address='a-5', s_phone='ph-5', s_acctbal=400),
        ]
    )
    target = mock_spark.createDataFrame(
        [
            Row(s_suppkey=1),
            Row(s_suppkey=2),
            Row(s_suppkey=3),
            Row(s_suppkey=4),
        ]
    )

    with pytest.raises(ColumnMismatchException) as exception:
        capture_mismatch_data_and_columns(source=source, target=target, key_columns=["s_suppkey"])

    assert str(exception.value) == (
        "source and target should have same columns for capturing the mismatch data\n"
        "columns missing in source: None\n"
        "columns missing in target: s_nationkey,s_name,s_address,s_phone,s_acctbal\n"
    )


def test_alias_column_str():
    column_list = ['col1', 'col2', 'col3']
    alias = 'source'
    actual = alias_column_str(alias=alias, columns=column_list)
    expected = ['source.col1', 'source.col2', 'source.col3']

    assert actual == expected
