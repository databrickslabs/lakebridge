import json
import logging
import tempfile
import uuid
from collections.abc import Generator
from dataclasses import asdict
from pathlib import Path

import pytest

from pyspark.sql import DataFrame

from databricks.labs.blueprint.paths import WorkspacePath
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import PermissionDenied
from databricks.sdk.service.catalog import TableInfo, SchemaInfo
from databricks.sdk.service.compute import DataSecurityMode, Kind

from databricks.labs.lakebridge.config import (
    DatabaseConfig,
    LakebridgeConfiguration,
    ReconcileConfig,
    ReconcileJobConfig,
    ReconcileMetadataConfig,
    TableRecon,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from databricks.labs.lakebridge.reconcile.recon_config import Table
from tests.integration.debug_envgetter import TestEnvGetter

logger = logging.getLogger(__name__)

DIAMONDS_COLUMNS = [
    ("carat", "DOUBLE"),
    ("cut", "STRING"),
    ("color", "STRING"),
    ("clarity", "STRING"),
]
DIAMONDS_ROWS_SQL = """
                    INSERT INTO {catalog}.{schema}.{table} (carat, cut, color, clarity) VALUES
                        (0.23, 'Ideal', 'E', 'SI2'),
                        (0.21, 'Premium', 'E', 'SI1'),
                        (0.23, 'Good', 'E', 'VS1'),
                        (0.29, 'Premium', 'I', 'VS2'),
                        (0.29, 'Gold', 'Invariant', 'VS22'),
                        (0.31, 'Good', 'J', 'SI2'); \
                    """


@pytest.fixture
def recon_catalog(make_catalog) -> str:
    try:
        catalog = make_catalog().name
        logger.info(f"Created catalog {catalog} for recon tests")
    except PermissionDenied as e:
        logger.warning("Could not create catalog for recon tests, using 'sandbox' instead", exc_info=e)
        catalog = "sandbox"

    return catalog


@pytest.fixture
def recon_schema(recon_catalog, make_schema) -> SchemaInfo:
    from_schema = make_schema(catalog_name=recon_catalog)
    logger.info(f"Created schema {from_schema.name} in catalog {recon_catalog} for recon tests")

    return from_schema


@pytest.fixture
def recon_tables(ws: WorkspaceClient, recon_schema: SchemaInfo, make_table) -> tuple[TableInfo, TableInfo]:
    src_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    tgt_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    logger.info(f"Created recon tables {src_table.name}, {tgt_table.name} in schema {recon_schema.name}")

    test_env = TestEnvGetter(True)
    warehouse = test_env.get("TEST_DEFAULT_WAREHOUSE_ID")

    for tbl in (src_table, tgt_table):
        sql = DIAMONDS_ROWS_SQL.format(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            table=tbl.name,
        )
        exc_response = ws.statement_execution.execute_statement(
            warehouse_id=warehouse,
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
            statement=sql,
        )
        logger.info(f"Inserted data into table {tbl.name} and got response {exc_response.status}")

    return src_table, tgt_table


@pytest.fixture
def recon_metadata(mock_spark, report_tables_schema) -> Generator[ReconcileMetadataConfig, None, None]:
    rand = uuid.uuid4().hex
    schema = f"recon_schema_{rand}"
    mock_spark.sql(f"CREATE SCHEMA {schema}")
    main_schema, metrics_schema, details_schema = report_tables_schema

    mock_spark.createDataFrame(data=[], schema=main_schema).write.saveAsTable(f"{schema}.MAIN")
    mock_spark.createDataFrame(data=[], schema=metrics_schema).write.saveAsTable(f"{schema}.METRICS")
    mock_spark.createDataFrame(data=[], schema=details_schema).write.saveAsTable(f"{schema}.DETAILS")

    yield ReconcileMetadataConfig(
        catalog=f"recon_catalog_{rand}",
        schema=schema,
        volume=f"recon_volume_{rand}",
    )

    mock_spark.sql(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
    (src_table, tgt_table) = recon_tables
    assert src_table.name
    assert tgt_table.name

    return TableRecon(
        [
            Table(
                source_name=src_table.name,
                target_name=tgt_table.name,
                join_columns=["color", "clarity"],
            )
        ]
    )


@pytest.fixture
def recon_config(make_cluster, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    cluster = (
        make_cluster(
            data_security_mode=DataSecurityMode.DATA_SECURITY_MODE_AUTO,
            kind=Kind.CLASSIC_PREVIEW,
            num_workers=2,
        )
        .result()
        .cluster_id
    )
    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        data_source="databricks",
        report_type="all",
        secret_scope="NOT_NEEDED",
        database_config=DatabaseConfig(
            source_catalog=recon_schema.catalog_name,
            source_schema=recon_schema.name,
            target_catalog=recon_schema.catalog_name,
            target_schema=recon_schema.name,
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name
        ),
        job_overrides=deployment_overrides,
    )


@pytest.fixture
def recon_config_filename(recon_config: ReconcileConfig) -> str:
    source_catalog_or_schema = (
        recon_config.database_config.source_catalog
        if recon_config.database_config.source_catalog
        else recon_config.database_config.source_schema
    )
    return f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"


@pytest.fixture
def application_context(
    application_ctx: ApplicationContext,
    recon_config: ReconcileConfig,
    recon_config_filename: str,
    recon_table_config: TableRecon,
) -> Generator[ApplicationContext, None, None]:
    logger.info("Setting up application context for recon tests")
    config = LakebridgeConfiguration(None, recon_config)
    ws = application_ctx.workspace_client
    logger.info("Installing app and recon configuration into workspace")
    application_ctx.installation.save(recon_config)
    application_ctx.installation.upload(recon_config_filename, json.dumps(asdict(recon_table_config)).encode())
    application_ctx.workspace_installation.install(config)

    logger.info("Application context setup complete for recon tests")
    yield application_ctx

    logger.info("Tearing down application context for recon tests")
    application_ctx.workspace_installation.uninstall(config)
    if WorkspacePath(ws, application_ctx.installation.install_folder()).exists():
        application_ctx.installation.remove()
    logger.info("Application context teardown complete for recon tests")


class FakeReconIntermediatePersist(AbstractReconIntermediatePersist):
    @property
    def base_dir(self) -> Path:
        return Path(tempfile.gettempdir())

    @property
    def is_serverless(self) -> bool:
        return False

    def write_and_read_df_with_volumes(
        self,
        df: DataFrame,
    ) -> DataFrame:
        return df
