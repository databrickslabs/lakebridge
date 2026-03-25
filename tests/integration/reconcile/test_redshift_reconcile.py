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
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist
from tests.integration.debug_envgetter import TestEnvGetter
from tests.integration.reconcile.connectors.test_read_schema import RedshiftDataSourceUnderTest


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


@pytest.mark.skip(reason="Requires Redshift connectivity and a Databricks cluster.")
def test_redshift_db_reconcile(mock_spark, mock_workspace_client, tmp_path):
    test_env = TestEnvGetter(True)
    cluster = test_env.get("TEST_REDSHIFT_CLUSTER_ID")
    host = test_env.get("TEST_REDSHIFT_DATABRICKS_HOST")
    databricks = DatabricksSession.builder.host(host).clusterId(cluster).profile("redshift_test").getOrCreate()
    databricks_data_source = DatabricksDataSourceUnderTest(databricks, mock_workspace_client, mock_spark)
    redshift_data_source = RedshiftDataSourceUnderTest(mock_spark, mock_workspace_client)
    report = "row"
    db_config = DatabaseConfig(
        source_schema="public",
        target_catalog="lakebridge",
        target_schema="default",
    )
    reconcile_config = ReconcileConfig(
        data_source="redshift",
        report_type=report,
        secret_scope="not used",
        database_config=db_config,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
    )
    recon = Reconciliation(
        source=redshift_data_source,
        target=databricks_data_source,
        database_config=db_config,
        report_type=report,
        schema_comparator=SchemaCompare(mock_spark),
        source_engine=get_dialect("redshift"),
        spark=mock_spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
        intermediate_persist=FakeReconIntermediatePersist(),
    )
    recon_capture = ReconCapture(
        database_config=db_config,
        recon_id="test_redshift_db_reconcile",
        report_type=report,
        source_dialect=get_dialect("redshift"),
        ws=mock_workspace_client,
        spark=mock_spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
        local_test_run=True,
    )
    with patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)):
        _, data_reconcile_output = TriggerReconService.recon_one(
            reconciler=recon,
            recon_capture=recon_capture,
            reconcile_config=reconcile_config,
            table_conf=Table(
                source_name="diamonds",
                target_name="diamonds",
                join_columns=["color", "clarity"],
            ),
        )

        assert not data_reconcile_output.missing_in_src_count
        assert not data_reconcile_output.missing_in_tgt_count
