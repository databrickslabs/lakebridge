import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyspark.sql import DataFrame

from databricks.labs.lakebridge.reconcile.compare import (
    capture_mismatch_data_and_columns,
    reconcile_agg_data_per_rule,
)
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from databricks.labs.lakebridge.reconcile.recon_config import AggregateRule


class RecordingReconIntermediatePersist(AbstractReconIntermediatePersist):
    def __init__(self) -> None:
        self.write_calls = 0

    @property
    def base_dir(self) -> Path:
        return Path(tempfile.gettempdir())

    @property
    def is_serverless(self) -> bool:
        return True

    def write_and_read_df_with_volumes(self, df: DataFrame) -> DataFrame:
        self.write_calls += 1
        return df


def _mock_capture_df() -> MagicMock:
    capture_df = MagicMock()
    capture_df.columns = ["s_suppkey", "s_name"]
    return capture_df


def test_capture_mismatch_data_and_columns_skips_persistence_when_not_provided() -> None:
    mismatch_df = MagicMock()
    with (
        patch("databricks.labs.lakebridge.reconcile.compare._build_capture_df", return_value=_mock_capture_df()),
        patch("databricks.labs.lakebridge.reconcile.compare._get_mismatch_df", return_value=mismatch_df),
        patch("databricks.labs.lakebridge.reconcile.compare._get_mismatch_columns", return_value=[]),
    ):
        capture_mismatch_data_and_columns(
            source=MagicMock(),
            target=MagicMock(),
            key_columns=["s_suppkey"],
        )


def test_capture_mismatch_data_and_columns_uses_persistence_when_provided() -> None:
    mismatch_df = MagicMock()
    recording = RecordingReconIntermediatePersist()
    with (
        patch("databricks.labs.lakebridge.reconcile.compare._build_capture_df", return_value=_mock_capture_df()),
        patch("databricks.labs.lakebridge.reconcile.compare._get_mismatch_df", return_value=mismatch_df),
        patch("databricks.labs.lakebridge.reconcile.compare._get_mismatch_columns", return_value=[]),
    ):
        capture_mismatch_data_and_columns(
            source=MagicMock(),
            target=MagicMock(),
            key_columns=["s_suppkey"],
            persistence=recording,
        )

    assert recording.write_calls == 1


def test_reconcile_agg_data_per_rule_materializes_rule_outputs() -> None:
    joined_df = MagicMock()
    joined_df.columns = ["source_min_s_acctbal", "target_min_s_acctbal"]
    joined_df_with_rule_cols = MagicMock()
    joined_df.select.return_value = joined_df_with_rule_cols

    mismatch_df = MagicMock()
    missing_in_src = MagicMock()
    missing_in_tgt = MagicMock()
    missing_in_src.count.return_value = 0
    missing_in_tgt.count.return_value = 0
    missing_in_src.limit.return_value = missing_in_src
    missing_in_tgt.limit.return_value = missing_in_tgt

    joined_df_with_rule_cols.filter.return_value.select.side_effect = [missing_in_src, missing_in_tgt]

    rule = AggregateRule(
        agg_type="min",
        agg_column="s_acctbal",
        group_by_columns=None,
        group_by_columns_as_str="NA",
    )
    recording = RecordingReconIntermediatePersist()

    with patch(
        "databricks.labs.lakebridge.reconcile.compare._get_mismatch_agg_data",
        return_value=mismatch_df,
    ):
        reconcile_agg_data_per_rule(
            joined_df,
            joined_df.columns,
            joined_df.columns,
            rule,
            recording,
        )

    assert recording.write_calls == 3


def test_compute_threshold_comparison_materializes_mismatched_rows() -> None:
    from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
    from databricks.labs.lakebridge.reconcile.recon_config import ColumnThresholds, Table
    from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation

    recording = RecordingReconIntermediatePersist()
    table_conf = Table(
        source_name="supplier",
        target_name="target_supplier",
        column_thresholds=[
            ColumnThresholds(column_name="s_acctbal", lower_bound="0", upper_bound="100", type="int"),
        ],
    )
    threshold_result = MagicMock()
    filtered_df = MagicMock()
    threshold_result.filter.return_value = filtered_df
    filtered_df.count.return_value = 2

    reconciler = Reconciliation(
        source=MagicMock(),
        target=MagicMock(),
        database_config=DatabaseConfig("src_cat", "src_schema", "tgt_cat", "tgt_schema"),
        report_type="data",
        schema_comparator=MagicMock(),
        source_engine=MagicMock(),
        spark=MagicMock(),
        metadata_config=ReconcileMetadataConfig(),
        intermediate_persist=recording,
    )
    reconciler._target.read_data = MagicMock(return_value=threshold_result)

    with patch(
        "databricks.labs.lakebridge.reconcile.reconciliation.ThresholdQueryBuilder.build_comparison_query",
        return_value="SELECT 1",
    ):
        output = reconciler._compute_threshold_comparison(table_conf, src_schema=[])

    assert recording.write_calls == 1
    assert output.threshold_mismatch_count == 2
    threshold_result.filter.assert_called_once()
