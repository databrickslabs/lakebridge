from unittest.mock import MagicMock, patch

from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.recon_config import ColumnThresholds, Schema, Table
from databricks.labs.lakebridge.reconcile.recon_output_config import DataReconcileOutput, MismatchOutput
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def _build_reconciliation(target=None):
    return Reconciliation(
        source=MagicMock(),
        target=target or MagicMock(),
        database_config=DatabaseConfig(
            source_schema="src_schema",
            target_catalog="tgt_catalog",
            target_schema="tgt_schema",
        ),
        report_type="all",
        schema_comparator=MagicMock(),
        source_engine=get_dialect("databricks"),
        spark=MagicMock(),
        metadata_config=ReconcileMetadataConfig(),
        intermediate_persist=MagicMock(is_serverless=False),
    )


def test_reconcile_data_passes_max_sample_size_to_sampler_factory():
    recon = _build_reconciliation()
    table_conf = Table(
        source_name="src",
        target_name="tgt",
        join_columns=["id"],
        max_sample_size=999,
    )
    schema_item = Schema(
        column_name="id",
        data_type="int",
        ansi_normalized_column_name="`id`",
        source_normalized_column_name="`id`",
    )
    mocked_output = DataReconcileOutput(
        mismatch_count=1,
        missing_in_src_count=0,
        missing_in_tgt_count=0,
        mismatch=MismatchOutput(mismatch_df=MagicMock(), mismatch_columns=[]),
    )

    with (
        patch("databricks.labs.lakebridge.reconcile.reconciliation.HashQueryBuilder"),
        patch(
            "databricks.labs.lakebridge.reconcile.reconciliation.reconcile_data",
            return_value=mocked_output,
        ),
        patch("databricks.labs.lakebridge.reconcile.reconciliation.SamplingQueryBuilder"),
        patch("databricks.labs.lakebridge.reconcile.reconciliation.SamplerFactory") as factory_mock,
        patch(
            "databricks.labs.lakebridge.reconcile.reconciliation.capture_mismatch_data_and_columns",
            return_value=MagicMock(),
        ),
    ):
        factory_mock.get_sampler.return_value.sample.return_value = MagicMock()
        recon.reconcile_data(table_conf, [schema_item], [schema_item])

    _, kwargs = factory_mock.get_sampler.call_args
    assert kwargs["max_sample_size"] == 999


def test_reconcile_data_limits_threshold_df_with_max_sample_size():
    mismatched_df = MagicMock()
    mismatched_df.count.return_value = 10
    threshold_result = MagicMock()
    threshold_result.filter.return_value = mismatched_df

    target = MagicMock()
    target.read_data.return_value = threshold_result
    recon = _build_reconciliation(target=target)

    table_conf = Table(
        source_name="src",
        target_name="tgt",
        join_columns=["id"],
        column_thresholds=[
            ColumnThresholds(column_name="s_acctbal", lower_bound="0", upper_bound="100", type="int"),
        ],
        max_sample_size=200,
    )
    schema_item = Schema(
        column_name="s_acctbal",
        data_type="int",
        ansi_normalized_column_name="`s_acctbal`",
        source_normalized_column_name="`s_acctbal`",
    )
    mocked_output = DataReconcileOutput(
        mismatch_count=0,
        missing_in_src_count=0,
        missing_in_tgt_count=0,
        mismatch=MismatchOutput(mismatch_df=None, mismatch_columns=[]),
    )

    with (
        patch("databricks.labs.lakebridge.reconcile.reconciliation.HashQueryBuilder"),
        patch(
            "databricks.labs.lakebridge.reconcile.reconciliation.reconcile_data",
            return_value=mocked_output,
        ),
        patch("databricks.labs.lakebridge.reconcile.reconciliation.ThresholdQueryBuilder") as builder_mock,
    ):
        builder_mock.return_value.build_threshold_query.return_value = "SELECT 1"
        builder_mock.return_value.build_comparison_query.return_value = "SELECT 1"
        recon.reconcile_data(table_conf, [schema_item], [schema_item])

    mismatched_df.limit.assert_called_once_with(200)
