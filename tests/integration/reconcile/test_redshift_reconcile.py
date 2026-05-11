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
from databricks.labs.lakebridge.reconcile.connectors import redshift as redshift_module
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.recon_capture import ReconCapture
from databricks.labs.lakebridge.reconcile.recon_config import Table, JdbcReaderOptions, Schema
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist
from tests.integration.debug_envgetter import TestEnvGetter


class RedshiftDataSourceUnderTest(RedshiftDataSource):
    _DRIVER_CLASS = "com.amazon.redshift.Driver"
    _DEFAULT_DATABASE = "dev"

    def __init__(self, spark):
        # reader is unused — this subclass fully overrides read_data/get_schema with JDBC
        reader = RemoteQueryReader(spark, "NOT USED")
        super().__init__(get_dialect("redshift"), reader)
        self._spark = spark
        self._test_env = TestEnvGetter(True)

    @property
    def _get_jdbc_url(self) -> str:
        host = self._test_env.get("REDSHIFT_HOST")
        port = self._test_env.get("REDSHIFT_PORT")
        return f"jdbc:redshift://{host}:{port}/{RedshiftDataSourceUnderTest._DEFAULT_DATABASE}"

    def _jdbc_reader(self, query: str) -> DataFrameReader:
        user = self._test_env.get("REDSHIFT_USER")
        password = self._test_env.get("REDSHIFT_PASS")
        return self._spark.read.format("jdbc").options(
            **{
                "driver": RedshiftDataSourceUnderTest._DRIVER_CLASS,
                "url": self._get_jdbc_url,
                "user": user,
                "password": password,
                "dbtable": query,
            }
        )

    def read_data(
        self,
        catalog: str,
        schema: str,
        table: str,
        query: str,
        options: JdbcReaderOptions | None,
    ):
        table_query = query.replace("%(tbl)s", f"{schema}.{table}").replace(":tbl", f"{schema}.{table}")
        return self._jdbc_reader(f"({table_query}) as tmp").load()

    def get_schema(
        self,
        catalog: str,
        schema: str,
        table: str,
        normalize: bool = True,
    ) -> list[Schema]:
        import re

        schema_query = re.sub(
            r'\s+',
            ' ',
            redshift_module._SCHEMA_QUERY.format(schema=schema, table=table),
        )
        rows = self._jdbc_reader(f"({schema_query}) as tmp").load().collect()
        return [self._map_meta_column(r, normalize) for r in rows]


class DatabricksDataSourceUnderTest(DatabricksDataSource):
    def __init__(self, databricks, ws, local_spark):
        super().__init__(get_dialect("databricks"), databricks, ws)
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
def test_redshift_db_reconcile(spark, mock_workspace_client, tmp_path):
    test_env = TestEnvGetter(True)
    cluster = test_env.get("TEST_REDSHIFT_CLUSTER_ID")
    host = test_env.get("TEST_REDSHIFT_DATABRICKS_HOST")
    databricks = DatabricksSession.builder.host(host).clusterId(cluster).profile("redshift_test").getOrCreate()
    databricks_data_source = DatabricksDataSourceUnderTest(databricks, mock_workspace_client, spark)
    redshift_data_source = RedshiftDataSourceUnderTest(spark)
    report = "row"
    source_dialect = get_dialect("redshift")
    metadata_config = ReconcileMetadataConfig(catalog="tmp", schema="reconcile")
    db_config = DatabaseConfig(
        source_catalog="dev",
        source_schema="public",
        target_catalog="lakebridge",
        target_schema="default",
    )
    reconcile_config = ReconcileConfig(
        report_type=report,
        source=SourceConnectionConfig(
            dialect="redshift",
            catalog="dev",
            schema="public",
            uc_connection_name="not used",
        ),
        target=TargetConnectionConfig(
            catalog="lakebridge",
            schema="default",
        ),
        metadata_config=metadata_config,
    )
    recon = Reconciliation(
        source=redshift_data_source,
        target=databricks_data_source,
        database_config=db_config,
        report_type=report,
        schema_comparator=SchemaCompare(spark),
        source_engine=source_dialect,
        spark=spark,
        metadata_config=metadata_config,
        intermediate_persist=FakeReconIntermediatePersist(),
    )
    recon_capture = ReconCapture(
        database_config=db_config,
        recon_id="test_redshift_db_reconcile",
        report_type=report,
        source_dialect=source_dialect,
        ws=mock_workspace_client,
        spark=spark,
        metadata_config=metadata_config,
    )
    table_conf = Table(
        source_name="diamonds",
        target_name="diamonds",
        join_columns=["color", "clarity"],
    )

    with patch("databricks.labs.lakebridge.reconcile.utils.generate_volume_path", return_value=str(tmp_path)):
        _, data_reconcile_output = TriggerReconService.recon_one(
            reconciler=recon,
            recon_capture=recon_capture,
            reconcile_config=reconcile_config,
            table_conf=table_conf,
        )

        assert not data_reconcile_output.missing_in_src_count
        assert not data_reconcile_output.missing_in_tgt_count
