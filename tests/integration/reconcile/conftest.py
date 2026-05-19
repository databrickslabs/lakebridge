import datetime as dt
import json
import logging
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
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
    LakebridgeConfiguration,
    ReconcileConfig,
    ReconcileJobConfig,
    ReconcileMetadataConfig,
    SourceConnectionConfig,
    TargetConnectionConfig,
    TableRecon,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_capture import AbstractReconIntermediatePersist
from databricks.labs.lakebridge.reconcile.recon_config import Table

logger = logging.getLogger(__name__)

DIAMONDS_COLUMNS = [
    ("carat", "DOUBLE"),
    ("cut", "STRING"),
    ("color", "STRING"),
    ("clarity", "STRING"),
    ("mined_at", "DATE"),
]
DIAMONDS_ROWS_SQL = """
                    INSERT INTO {catalog}.{schema}.{table} (carat, cut, color, clarity, mined_at) VALUES
                        (0.23, 'Ideal', 'E', 'SI2', '2000-01-01'),
                        (0.21, 'Premium', 'E', 'SI1', '2000-01-01'),
                        (0.23, 'Good', 'E', 'VS1', '2000-01-01'),
                        (0.29, 'Premium', 'I', 'VS2', NULL),
                        (0.29, 'Gold', 'Invariant', 'VS22', '2000-01-01'),
                        (0.31, 'Good', 'J', 'SI2', '2000-01-01'); \
                    """

TSQL_CONNECTION = "sqlserver_sandbox"
TSQL_CATALOG = "labs_azure_sandbox_remorph"
TSQL_SCHEMA = "dbo"
TSQL_TABLE = "diamonds"
SNOWFLAKE_CONNECTION = "sf_sandbox"
SNOWFLAKE_CATALOG = "INTEGRATION"
SNOWFLAKE_SCHEMA = "LAKEBRIDGE"
SNOWFLAKE_TABLE = "DIAMONDS"
REDSHIFT_CONNECTION = "sandbox_labs_tool_redshift"
REDSHIFT_CATALOG = "labs"
REDSHIFT_SCHEMA = "lakebridge"
REDSHIFT_TABLE = "diamonds"
ORACLE_CONNECTION = "oracle_sandbox"
ORACLE_SRV = "orcl"
ORACLE_SCHEMA = "ADMIN"
ORACLE_TABLE = "DIAMONDS"


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
def recon_tables(ws: WorkspaceClient, recon_schema: SchemaInfo, make_table, test_env) -> tuple[TableInfo, TableInfo]:
    src_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    tgt_table = make_table(
        catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, columns=DIAMONDS_COLUMNS
    )
    logger.info(f"Created recon tables {src_table.name}, {tgt_table.name} in schema {recon_schema.name}")

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
def recon_metadata(spark, recon_schema, make_volume, report_tables_schema) -> ReconcileMetadataConfig:
    assert recon_schema.catalog_name
    assert recon_schema.name

    prefix = f"{recon_schema.catalog_name}.{recon_schema.name}"
    main_schema, metrics_schema, details_schema = report_tables_schema
    spark.createDataFrame(data=[], schema=main_schema).write.saveAsTable(f"{prefix}.MAIN")
    spark.createDataFrame(data=[], schema=metrics_schema).write.saveAsTable(f"{prefix}.METRICS")
    spark.createDataFrame(data=[], schema=details_schema).write.saveAsTable(f"{prefix}.DETAILS")

    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)
    return ReconcileMetadataConfig(catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name)


@pytest.fixture
def databricks_recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
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
def tsql_recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
    (_, tgt_table) = recon_tables
    assert tgt_table.name

    return TableRecon(
        [
            Table(
                source_name=TSQL_TABLE,
                target_name=tgt_table.name,
                join_columns=["color", "clarity"],
            )
        ]
    )


@pytest.fixture
def snowflake_recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
    (_, tgt_table) = recon_tables
    assert tgt_table.name

    return TableRecon(
        [
            Table(
                source_name=SNOWFLAKE_TABLE,
                target_name=tgt_table.name,
                join_columns=["color", "clarity"],
            )
        ]
    )


@pytest.fixture
def oracle_recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
    (_, tgt_table) = recon_tables
    assert tgt_table.name

    return TableRecon(
        [
            Table(
                source_name=ORACLE_TABLE,
                target_name=tgt_table.name,
                join_columns=["color", "clarity"],
            )
        ]
    )


@pytest.fixture
def recon_cluster(make_cluster, test_env) -> str:
    pool_id = test_env.get("TEST_INSTANCE_POOL_ID")
    cluster = make_cluster(
        single_node=True,
        data_security_mode=DataSecurityMode.DATA_SECURITY_MODE_AUTO,
        kind=Kind.CLASSIC_PREVIEW,
        instance_pool_id=pool_id,
        spark_version="17.3.x-scala2.13",
    ).result(timeout=dt.timedelta(minutes=10))
    assert cluster.cluster_id
    return cluster.cluster_id


@pytest.fixture
def databricks_recon_config(
    recon_cluster: str, recon_schema: SchemaInfo, recon_metadata: ReconcileMetadataConfig
) -> ReconcileConfig:
    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=recon_cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="databricks",
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        target=TargetConnectionConfig(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        metadata_config=recon_metadata,
        job_overrides=deployment_overrides,
    )


@pytest.fixture
def tsql_recon_config(recon_cluster: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=recon_cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="tsql",
            catalog=TSQL_CATALOG,
            schema=TSQL_SCHEMA,
            uc_connection_name=TSQL_CONNECTION,
        ),
        target=TargetConnectionConfig(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name
        ),
        job_overrides=deployment_overrides,
    )


@pytest.fixture
def snowflake_recon_config(recon_cluster: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=recon_cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog=SNOWFLAKE_CATALOG,
            schema=SNOWFLAKE_SCHEMA,
            uc_connection_name=SNOWFLAKE_CONNECTION,
        ),
        target=TargetConnectionConfig(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name
        ),
        job_overrides=deployment_overrides,
    )


@pytest.fixture
def redshift_recon_table_config(recon_schema: SchemaInfo, recon_tables: tuple[TableInfo, TableInfo]) -> TableRecon:
    (_, tgt_table) = recon_tables
    assert tgt_table.name

    return TableRecon(
        [
            Table(
                source_name=REDSHIFT_TABLE,
                target_name=tgt_table.name,
                join_columns=["color", "clarity"],
            )
        ]
    )


@pytest.fixture
def redshift_recon_config(recon_cluster: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=recon_cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="redshift",
            catalog=REDSHIFT_CATALOG,
            schema=REDSHIFT_SCHEMA,
            uc_connection_name=REDSHIFT_CONNECTION,
        ),
        target=TargetConnectionConfig(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name
        ),
        job_overrides=deployment_overrides,
    )


@pytest.fixture
def oracle_recon_config(recon_cluster: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    deployment_overrides = ReconcileJobConfig(
        existing_cluster_id=recon_cluster,
        tags={"lakebridge": "reconcile_test"},
    )
    logger.info(f"Using recon job overrides: {deployment_overrides}")

    assert recon_schema.catalog_name
    assert recon_schema.name
    return ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="oracle",
            catalog=ORACLE_SRV,
            schema=ORACLE_SCHEMA,
            uc_connection_name=ORACLE_CONNECTION,
        ),
        target=TargetConnectionConfig(
            catalog=recon_schema.catalog_name,
            schema=recon_schema.name,
        ),
        metadata_config=ReconcileMetadataConfig(
            catalog=recon_schema.catalog_name, schema=recon_schema.name, volume=volume.name
        ),
        job_overrides=deployment_overrides,
    )


def recon_config_filename(recon_config: ReconcileConfig) -> str:
    connection_or_catalog = recon_config.source.uc_connection_name or recon_config.source.catalog
    return f"recon_config_{recon_config.source.dialect}_{connection_or_catalog}_{recon_config.report_type}.json"


@contextmanager
def generate_recon_application_context(
    application_ctx: ApplicationContext,
    recon_config: ReconcileConfig,
    recon_table_config: TableRecon,
) -> Generator[ApplicationContext]:
    logger.info("Setting up application context for recon tests")
    config = LakebridgeConfiguration(None, recon_config, None)
    ws = application_ctx.workspace_client
    logger.info("Installing app and recon configuration into workspace")
    application_ctx.installation.save(recon_config)
    filename = recon_config_filename(recon_config)
    application_ctx.installation.upload(filename, json.dumps(asdict(recon_table_config)).encode())
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
        return True
        # treat everything as serverless to avoid using cache completely.
        # the fallback to cache is stubbed as well

    def write_and_read_df_with_volumes(
        self,
        df: DataFrame,
    ) -> DataFrame:
        return df
