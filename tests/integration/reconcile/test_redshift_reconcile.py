from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    ReconcileMetadataConfig,
    ReconcileConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
)
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.redshift import RedshiftDataSource
from databricks.labs.lakebridge.reconcile.connectors.remote_query_reader import RemoteQueryReader
from databricks.labs.lakebridge.reconcile.recon_capture import ReconCapture
from databricks.labs.lakebridge.reconcile.recon_config import Table, Transformation
from databricks.labs.lakebridge.reconcile.reconciliation import Reconciliation
from databricks.labs.lakebridge.reconcile.schema_compare import SchemaCompare
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.transpiler.sqlglot.dialect_utils import get_dialect
from tests.integration.reconcile.conftest import FakeReconIntermediatePersist

REDSHIFT_CONNECTION = "sandbox_labs_tool_redshift"
REDSHIFT_CATALOG = "labs"
REDSHIFT_SCHEMA = "lakebridge"
TARGET_CATALOG = "sandbox"
TARGET_SCHEMA = "test_target"
TABLE_NAME = "diamonds"
META_CATALOG = "sandbox"
META_SCHEMA = "test_target"


def test_redshift_db_reconcile(spark, ws):
    redshift_data_source = RedshiftDataSource(
        get_dialect("redshift"),
        RemoteQueryReader(spark, REDSHIFT_CONNECTION),
    )
    databricks_data_source = DatabricksDataSource(get_dialect("databricks"), spark, ws)
    report = "row"
    source_dialect = get_dialect("redshift")
    metadata_config = ReconcileMetadataConfig(catalog=META_CATALOG, schema=META_SCHEMA)
    db_config = DatabaseConfig(
        source_catalog=REDSHIFT_CATALOG,
        source_schema=REDSHIFT_SCHEMA,
        target_catalog=TARGET_CATALOG,
        target_schema=TARGET_SCHEMA,
    )
    reconcile_config = ReconcileConfig(
        report_type=report,
        source=SourceConnectionConfig(
            dialect="redshift",
            catalog=REDSHIFT_CATALOG,
            schema=REDSHIFT_SCHEMA,
            uc_connection_name=REDSHIFT_CONNECTION,
        ),
        target=TargetConnectionConfig(
            catalog=TARGET_CATALOG,
            schema=TARGET_SCHEMA,
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
        ws=ws,
        spark=spark,
        metadata_config=metadata_config,
    )
    table_conf = Table(
        source_name=TABLE_NAME,
        target_name=TABLE_NAME,
        join_columns=["color", "clarity"],
        # Normalize the float-typed `carat` column so source/target string
        # representations are identical (Redshift renders 8-digit precision;
        # Databricks rounds to display). Also exercises the Transformation feature.
        transformations=[
            Transformation(
                column_name="carat",
                source='CAST("carat" AS DECIMAL(4,2))',
                target="CAST(`carat` AS DECIMAL(4,2))",
            ),
        ],
    )

    _, data_reconcile_output = TriggerReconService.recon_one(
        reconciler=recon,
        recon_capture=recon_capture,
        reconcile_config=reconcile_config,
        table_conf=table_conf,
    )

    assert not data_reconcile_output.missing_in_src_count
    assert not data_reconcile_output.missing_in_tgt_count
