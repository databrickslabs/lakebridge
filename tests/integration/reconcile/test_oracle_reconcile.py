from unittest.mock import patch

import pytest
from pyspark.sql import DataFrame

from databricks.connect import DatabricksSession
from databricks.labs.lakebridge.config import DatabaseConfig, ReconcileMetadataConfig, ReconcileConfig
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.recon_capture import ReconCapture
from databricks.labs.lakebridge.reconcile.recon_config import Table, JdbcReaderOptions
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.connectors.test_read_schema import OracleDataSourceUnderTest


class DatabricksDataSourceUnderTest(DatabricksDataSource):
    def __init__(self, databricks, ws, local_spark):
        super().__init__(get_dialect("databricks"), databricks, ws, "not used")
        self._local_spark = local_spark

    def read_data(
        self,
        catalog: str | None,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        data = super().read_data(catalog, schema, table, query, options).collect()
        return self._local_spark.createDataFrame(data)


@pytest.mark.skip(reason="Requires Oracle DB running locally and a databricks cluster to connect to.")
def test_oracle_db_reconcile(mock_spark, mock_workspace_client, tmp_path):
    databricks = DatabricksSession.builder.getOrCreate()
    databricks_data_source = DatabricksDataSourceUnderTest(databricks, mock_workspace_client, mock_spark)
    oracle_data_source = OracleDataSourceUnderTest(mock_spark, mock_workspace_client)
    report = "row"
    db_config = DatabaseConfig(
        source_schema="SYSTEM",
        target_catalog="main",
        target_schema="lakebridge",
    )
    reconcile_config = ReconcileConfig(
        data_source="oracle",
        report_type=report,
        secret_scope="not used",
        database_config=db_config,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
    )
    recon = Reconciliation(
        source=oracle_data_source,
        target=databricks_data_source,
        database_config=db_config,
        report_type=report,
        schema_comparator=SchemaCompare(mock_spark),
        source_engine=get_dialect("oracle"),
        spark=mock_spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
    )
    recon_capture = ReconCapture(
        database_config=db_config,
        recon_id="test_oracle_db_reconcile",
        report_type=report,
        source_dialect=get_dialect("oracle"),
        ws=mock_workspace_client,
        spark=mock_spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
        local_test_run=True,
    )
    with patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)):
        _, data_reconcile_output = TriggerReconService.recon_one(
            spark=mock_spark,
            reconciler=recon,
            recon_capture=recon_capture,
            reconcile_config=reconcile_config,
            table_conf=Table(
                source_name="source_table",
                target_name="target_table",
                join_columns=["id"],
            ),
        )

        assert not data_reconcile_output.missing_in_src_count
        assert not data_reconcile_output.missing_in_tgt_count
