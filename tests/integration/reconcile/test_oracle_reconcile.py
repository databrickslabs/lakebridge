from unittest.mock import patch

import pytest
from pyspark.sql import DataFrame, DataFrameReader
from databricks.connect import DatabricksSession
from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    ReconcileMetadataConfig,
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.recon_capture import ReconCapture
from databricks.labs.lakebridge.reconcile.recon_config import Table, JdbcReaderOptions
from databricks.labs.lakebridge.reconcile.connectors.oracle import OracleDataSource
from databricks.labs.lakebridge.reconcile.recon_config import Schema
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist
from tests.integration.debug_envgetter import TestEnvGetter


class OracleDataSourceUnderTest(OracleDataSource):
    def __init__(self, spark):
        # reader is unused — this subclass fully overrides read_data/get_schema with JDBC
        reader = RemoteQueryReader(spark, "NOT USED")
        super().__init__(get_dialect("oracle"), reader)
        self._spark = spark
        self._test_env = TestEnvGetter(False)

    @property
    def _get_jdbc_url(self) -> str:
        return self._test_env.get("TEST_ORACLE_JDBC")

    def _jdbc_reader(self, query: str) -> DataFrameReader:
        user = self._test_env.get("TEST_ORACLE_USER")
        password = self._test_env.get("TEST_ORACLE_PASSWORD")
        return self._spark.read.format("JDBC").options(
            **{"driver": "", "url": self._get_jdbc_url, "user": user, "password": password, "dbtable": query}
        )

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ):
        table_query = query.replace(":tbl", table)
        return self._jdbc_reader(table_query).load().collect()

    def get_schema(
        self,
        catalog: str,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        rows = self._jdbc_reader(OracleDataSource._SCHEMA_QUERY).load().collect()
        return [self._map_meta_column(r, normalize) for r in rows]


class DatabricksDataSourceUnderTest(DatabricksDataSource):
    def __init__(self, databricks, ws, local_spark):
        super().__init__(get_dialect("databricks"), databricks, ws)
        self._local_spark = local_spark

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ) -> DataFrame:
        data = super().read_data(catalog, schema, table, query, options).collect()
        return self._local_spark.createDataFrame(data)


@pytest.mark.skip(reason="Requires Oracle DB running locally and a databricks cluster to connect to.")
def test_oracle_db_reconcile(spark, mock_workspace_client, tmp_path):
    test_env = TestEnvGetter(True)
    cluster = test_env.get("TEST_USER_ISOLATION_CLUSTER_ID")
    host = test_env.get("DATABRICKS_HOST")
    databricks = DatabricksSession.builder.host(host).clusterId(cluster).getOrCreate()
    databricks_data_source = DatabricksDataSourceUnderTest(databricks, mock_workspace_client, spark)
    oracle_data_source = OracleDataSourceUnderTest(spark)
    report = "row"
    db_config = DatabaseConfig(
        source_catalog="",
        source_schema="SYSTEM",
        target_catalog="main",
        target_schema="lakebridge",
    )
    reconcile_config = ReconcileConfig(
        report_type=report,
        source=SourceConnectionConfig(
            dialect="oracle",
            catalog="",
            schema="SYSTEM",
            uc_connection_name="not used",
        ),
        target=TargetConnectionConfig(
            catalog="main",
            schema="lakebridge",
        ),
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
    )
    recon = Reconciliation(
        source=oracle_data_source,
        target=databricks_data_source,
        database_config=db_config,
        report_type=report,
        schema_comparator=SchemaCompare(spark),
        source_engine=get_dialect("oracle"),
        spark=spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
        intermediate_persist=FakeReconIntermediatePersist(),
    )
    recon_capture = ReconCapture(
        database_config=db_config,
        recon_id="test_oracle_db_reconcile",
        report_type=report,
        source_dialect=get_dialect("oracle"),
        ws=mock_workspace_client,
        spark=spark,
        metadata_config=ReconcileMetadataConfig(catalog="tmp", schema="reconcile"),
    )
    with patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)):
        _, data_reconcile_output = TriggerReconService.recon_one(
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
