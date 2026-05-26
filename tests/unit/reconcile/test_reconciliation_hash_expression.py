from unittest.mock import create_autospec, patch

from pyspark.sql import SparkSession

from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig
from databricks.labs.lakebridge.reconcile.connectors.data_source import DataSource
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from databricks.labs.lakebridge.reconcile.recon_config import Table
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect


def _build_reconciliation(
    source_hash_expression: str | None = None,
    target_hash_expression: str | None = None,
) -> Reconciliation:
    # ``report_type='row'`` keeps reconcile_data's path narrow: it calls _get_reconcile_output
    # once and returns, so the test only needs to mock HashQueryBuilder + reconcile_data.
    return Reconciliation(
        source=create_autospec(DataSource),
        target=create_autospec(DataSource),
        database_config=DatabaseConfig("src_cat", "src_sch", "tgt_cat", "tgt_sch"),
        report_type="row",
        schema_comparator=create_autospec(SchemaCompare),
        source_engine=get_dialect("teradata"),
        spark=create_autospec(SparkSession),
        metadata_config=ReconcileMetadataConfig(),
        intermediate_persist=create_autospec(AbstractReconIntermediatePersist),
        source_hash_expression=source_hash_expression,
        target_hash_expression=target_hash_expression,
    )


def _run_and_capture_builder_calls(rec: Reconciliation) -> list:
    table_conf = Table(source_name="t", target_name="t", join_columns=["k"])
    with (
        patch("databricks.labs.lakebridge.reconcile.reconciliation.HashQueryBuilder") as mock_builder,
        patch("databricks.labs.lakebridge.reconcile.reconciliation.reconcile_data") as mock_reconcile_data,
    ):
        mock_builder.return_value.build_query.return_value = "SELECT 1"
        mock_reconcile_data.return_value = None
        rec.reconcile_data(table_conf, [], [])
        return list(mock_builder.call_args_list)


def test_reconcile_data_passes_hash_expression_to_builder() -> None:
    """Reconciliation should plumb the source/target hash_expression args into both
    HashQueryBuilder calls."""
    rec = _build_reconciliation(
        source_hash_expression="src_db.my_sha256({})",
        target_hash_expression="sha2({}, 256)",
    )

    builder_calls = _run_and_capture_builder_calls(rec)

    assert len(builder_calls) == 2
    assert builder_calls[0].kwargs["hash_expression"] == "src_db.my_sha256({})"
    assert builder_calls[1].kwargs["hash_expression"] == "sha2({}, 256)"


def test_reconcile_data_default_hash_expression_is_none() -> None:
    """When no hash_expression is supplied, both HashQueryBuilder calls receive None."""
    rec = _build_reconciliation()

    builder_calls = _run_and_capture_builder_calls(rec)

    assert len(builder_calls) == 2
    assert builder_calls[0].kwargs["hash_expression"] is None
    assert builder_calls[1].kwargs["hash_expression"] is None
