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
    return Reconciliation(
        source=create_autospec(DataSource),
        target=create_autospec(DataSource),
        database_config=DatabaseConfig("src_cat", "src_sch", "tgt_cat", "tgt_sch"),
        report_type="all",
        schema_comparator=create_autospec(SchemaCompare),
        source_engine=get_dialect("teradata"),
        spark=create_autospec(SparkSession),
        metadata_config=ReconcileMetadataConfig(),
        intermediate_persist=create_autospec(AbstractReconIntermediatePersist),
        source_hash_expression=source_hash_expression,
        target_hash_expression=target_hash_expression,
    )


def test_reconciliation_stores_hash_expressions() -> None:
    rec = _build_reconciliation(
        source_hash_expression="my_db.my_sha256({})",
        target_hash_expression="sha2({}, 256)",
    )
    assert rec._source_hash_expression == "my_db.my_sha256({})"  # pylint: disable=protected-access
    assert rec._target_hash_expression == "sha2({}, 256)"  # pylint: disable=protected-access


def test_reconciliation_hash_expressions_default_to_none() -> None:
    rec = _build_reconciliation()
    assert rec._source_hash_expression is None  # pylint: disable=protected-access
    assert rec._target_hash_expression is None  # pylint: disable=protected-access


def test_get_reconcile_output_passes_hash_expression_to_builder() -> None:
    """_get_reconcile_output should plumb the stored source/target hash_expression into the
    HashQueryBuilder calls for both layers."""
    rec = _build_reconciliation(
        source_hash_expression="src_db.my_sha256({})",
        target_hash_expression="sha2({}, 256)",
    )
    table_conf = Table(source_name="t", target_name="t", join_columns=["k"])

    with (
        patch("databricks.labs.lakebridge.reconcile.reconciliation.HashQueryBuilder") as mock_builder,
        patch("databricks.labs.lakebridge.reconcile.reconciliation.reconcile_data") as mock_reconcile_data,
    ):
        mock_builder.return_value.build_query.return_value = "SELECT 1"
        mock_reconcile_data.return_value = None

        rec._get_reconcile_output(table_conf, [], [])  # pylint: disable=protected-access

    builder_calls = mock_builder.call_args_list
    assert len(builder_calls) == 2
    assert builder_calls[0].kwargs["hash_expression"] == "src_db.my_sha256({})"
    assert builder_calls[1].kwargs["hash_expression"] == "sha2({}, 256)"
