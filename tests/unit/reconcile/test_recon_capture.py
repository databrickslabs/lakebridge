from unittest.mock import MagicMock

from databricks.labs.lakebridge.config import ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.recon_capture import (
    generate_final_reconcile_aggregate_output,
)


def _mock_spark() -> MagicMock:
    spark = MagicMock()
    df = MagicMock()
    df.collect.return_value = []
    spark.sql.return_value = df
    return spark


def test_generate_final_reconcile_aggregate_output_uses_metadata_schema_in_local_test_run() -> None:
    """local_test_run=True must read from metadata_config.schema, not the literal 'default'.

    Other places in recon_capture (generate_final_reconcile_output, ReconCapture.__init__)
    already use metadata_config.schema as the test-run prefix; the aggregate variant
    diverged and hard-coded 'default', which fails when callers pass a populated
    ReconcileMetadataConfig together with local_test_run=True.
    """
    spark = _mock_spark()
    metadata_config = ReconcileMetadataConfig(catalog="my_cat", schema="my_recon", volume="my_vol")

    generate_final_reconcile_aggregate_output(
        recon_id="r-1",
        spark=spark,
        metadata_config=metadata_config,
        local_test_run=True,
    )

    spark.sql.assert_called_once()
    sql = spark.sql.call_args.args[0]
    assert "my_recon.main" in sql
    assert "my_recon.aggregate_metrics" in sql
    # Regression: must not fall back to the hardcoded 'default' database
    assert "default.main" not in sql
    assert "default.aggregate_metrics" not in sql


def test_generate_final_reconcile_aggregate_output_uses_full_path_outside_local_test_run() -> None:
    """local_test_run=False keeps the catalog.schema prefix (production CLI path)."""
    spark = _mock_spark()
    metadata_config = ReconcileMetadataConfig(catalog="my_cat", schema="my_recon", volume="my_vol")

    generate_final_reconcile_aggregate_output(
        recon_id="r-2",
        spark=spark,
        metadata_config=metadata_config,
        local_test_run=False,
    )

    sql = spark.sql.call_args.args[0]
    assert "my_cat.my_recon.main" in sql
    assert "my_cat.my_recon.aggregate_metrics" in sql
