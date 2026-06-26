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
