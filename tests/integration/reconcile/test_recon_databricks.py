import json
import logging
from dataclasses import asdict

import pytest

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import TerminationTypeType

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    DatabaseConfig,
    ReconcileMetadataConfig,
    LakebridgeConfiguration,
    ReconcileJobConfig,
    TableRecon,
)
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME, Table
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk.service.catalog import TableInfo, SchemaInfo
from tests.integration.debug_envgetter import TestEnvGetter

logger = logging.getLogger(__name__)


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
def recon_config(watchdog_remove_after: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    test_env = TestEnvGetter(True)
    cluster = test_env.get("TEST_DEFAULT_CLUSTER_ID")
    tags = {"RemoveAfter": watchdog_remove_after}
    deployment_overrides = ReconcileJobConfig(existing_cluster_id=cluster, tags=tags)

    assert recon_schema.catalog_name
    assert recon_schema.name
    conf = ReconcileConfig(
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
    return conf


@pytest.fixture
def recon_config_filename(recon_config: ReconcileConfig) -> str:
    source_catalog_or_schema = (
        recon_config.database_config.source_catalog
        if recon_config.database_config.source_catalog
        else recon_config.database_config.source_schema
    )
    filename = f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"
    return filename


@pytest.fixture
def application_context(
    ws: WorkspaceClient, recon_config: ReconcileConfig, recon_config_filename: str, recon_table_config
):
    logger.info("Setting up application context for recon tests")
    config = LakebridgeConfiguration(None, recon_config)
    ctx = ApplicationContext(ws).replace(product_info=ProductInfo.for_testing(type(config)))

    logger.info("Installing app and recon configuration into workspace")
    ctx.installation.save(recon_config)
    ctx.installation.upload(recon_config_filename, json.dumps(asdict(recon_table_config)).encode())
    ctx.workspace_installation.install(config)

    logger.info("Application context setup complete for recon tests")
    yield ctx

    logger.info("Tearing down application context for recon tests")
    ctx.workspace_installation.uninstall(config)
    logger.info("Application context teardown complete for recon tests")


def test_recon_databricks_job_succeeds(application_context: ApplicationContext) -> None:
    recon_runner = ReconcileRunner(
        application_context.workspace_client,
        application_context.install_state,
    )
    run, _ = recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
    result = run.result()

    assert result.status
    assert result.status.termination_details
    assert result.status.termination_details.type
    assert result.status.termination_details.type.value == TerminationTypeType.SUCCESS.value
