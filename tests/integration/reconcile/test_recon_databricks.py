import json
import re
import logging
from dataclasses import asdict

import pytest

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import TerminationTypeType
from databricks.sdk.core import DatabricksError
from databricks.sdk.service.compute import DataSecurityMode, Kind

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
from databricks.sdk.service.catalog import TableInfo, SchemaInfo

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
def recon_config(make_cluster, watchdog_remove_after: str, recon_schema: SchemaInfo, make_volume) -> ReconcileConfig:
    volume = make_volume(catalog_name=recon_schema.catalog_name, schema_name=recon_schema.name, name=recon_schema.name)

    tags = {"RemoveAfter": watchdog_remove_after}
    cluster = (
        make_cluster(
            cluster_name="reconcile_e2e",
            data_security_mode=DataSecurityMode.DATA_SECURITY_MODE_AUTO,
            kind=Kind.CLASSIC_PREVIEW,
            num_workers=2,
            custom_tags=tags,
        )
        .result()
        .cluster_id
    )
    deployment_overrides = ReconcileJobConfig(existing_cluster_id=cluster, tags=tags)
    logger.info(f"Using recon job overrides: {deployment_overrides}")

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
    ctx = ApplicationContext(ws)

    logger.info("Installing app and recon configuration into workspace")
    ctx.installation.save(recon_config)
    ctx.installation.upload(recon_config_filename, json.dumps(asdict(recon_table_config)).encode())
    ctx.workspace_installation.install(config)

    logger.info("Application context setup complete for recon tests")
    yield ctx

    logger.info("Tearing down application context for recon tests")
    ctx.workspace_installation.uninstall(config)
    logger.info("Application context teardown complete for recon tests")


def debug_run_output(ctx: ApplicationContext, run_id: int) -> None:
    _ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

    def strip_ansi(unescaped: str) -> str:
        return _ansi_escape.sub("", unescaped)

    # pylint: disable = too-many-try-statements
    try:
        run_info = ctx.workspace_client.jobs.get_run(run_id)
        tasks = run_info.tasks if run_info.tasks else []
        logger.info(f"Reconcile job run had {len(tasks)} tasks")
        for task in tasks:
            if task.run_id:
                task_output = ctx.workspace_client.jobs.get_run_output(task.run_id)
                logger.info(f"Task {task.task_key} has error message: {task_output.error}")
                if task_output.error_trace:
                    logger.info(f"Task {task.task_key} has error trace:\n{strip_ansi(task_output.error_trace)}")
            else:
                logger.warning(f"Task {task.task_key} has no run_id")
    except DatabricksError:
        logger.exception("Failed to fetch run output")


def test_recon_databricks_job_succeeds(application_context: ApplicationContext) -> None:
    recon_runner = ReconcileRunner(
        application_context.workspace_client,
        application_context.install_state,
    )

    run = None
    try:
        run, _ = recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
        result = run.result()
    except Exception:
        if run:
            debug_run_output(application_context, run.run_id)
        raise

    logger.info(f"Reconcile job run result: {result.status}")
    assert result.status
    assert result.status.termination_details
    assert result.status.termination_details.type
    assert result.status.termination_details.type.value == TerminationTypeType.SUCCESS.value
