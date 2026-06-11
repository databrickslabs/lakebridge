import re
import logging

import pytest
from databricks.sdk.service.catalog import TableInfo
from databricks.sdk.service.jobs import TerminationTypeType
from databricks.sdk.core import DatabricksError

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import (
    DISCOVER_AND_AUTO_CONFIGURE_TABLES_OPERATION_NAME,
    RECONCILE_OPERATION_NAME,
)
from databricks.labs.lakebridge.reconcile.runner import ReconcileRunner
from tests.integration.reconcile.conftest import generate_recon_application_context

logger = logging.getLogger(__name__)


def _debug_run_output(ctx: ApplicationContext, run_id: int) -> None:
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


def _run_recon_e2e_spec(app_ctx: ApplicationContext):
    recon_runner = ReconcileRunner(
        app_ctx.workspace_client,
        app_ctx.install_state,
    )

    run = None
    try:
        run, _ = recon_runner.run(operation_name=RECONCILE_OPERATION_NAME)
        result = run.result()
    except Exception:
        if run:
            _debug_run_output(app_ctx, run.run_id)
        raise

    logger.info(f"Reconcile job run result: {result.status}")
    assert result.status
    assert result.status.termination_details
    assert result.status.termination_details.type
    assert result.status.termination_details.type.value == TerminationTypeType.SUCCESS.value


@pytest.mark.timeout(func_only=True)
def test_recon_databricks_job_succeeds(
    application_ctx: ApplicationContext,
    databricks_recon_config: ReconcileConfig,
    databricks_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(
        application_ctx, databricks_recon_config, databricks_recon_table_config
    ) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


@pytest.mark.timeout(func_only=True)
def test_recon_sql_server_job_succeeds(
    application_ctx: ApplicationContext, tsql_recon_config: ReconcileConfig, tsql_recon_table_config: TableRecon
) -> None:
    with generate_recon_application_context(application_ctx, tsql_recon_config, tsql_recon_table_config) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


@pytest.mark.timeout(func_only=True)
def test_recon_snowflake_job_succeeds(
    application_ctx: ApplicationContext,
    snowflake_recon_config: ReconcileConfig,
    snowflake_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(
        application_ctx, snowflake_recon_config, snowflake_recon_table_config
    ) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


@pytest.mark.timeout(func_only=True)
def test_recon_redshift_job_succeeds(
    application_ctx: ApplicationContext,
    redshift_recon_config: ReconcileConfig,
    redshift_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(
        application_ctx, redshift_recon_config, redshift_recon_table_config
    ) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


@pytest.mark.timeout(func_only=True)
def test_recon_oracle_job_succeeds(
    application_ctx: ApplicationContext,
    oracle_recon_config: ReconcileConfig,
    oracle_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(application_ctx, oracle_recon_config, oracle_recon_table_config) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


@pytest.mark.timeout(func_only=True)
def test_recon_teradata_job_succeeds(
    application_ctx: ApplicationContext,
    teradata_recon_config: ReconcileConfig,
    teradata_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(
        application_ctx, teradata_recon_config, teradata_recon_table_config
    ) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


def test_auto_configure_tables_writes_table_recon_config(
    application_ctx: ApplicationContext,
    databricks_recon_config: ReconcileConfig,
    recon_tables: tuple[TableInfo, TableInfo],
) -> None:
    """E2E for the auto-configure-tables operation. The TableRecon file is NOT pre-uploaded.
    The job writes it and assert it contains the auto-matched tables.
    """
    with generate_recon_application_context(application_ctx, databricks_recon_config):
        recon_runner = ReconcileRunner(application_ctx.workspace_client, application_ctx.install_state)
        run = None
        try:
            run, _ = recon_runner.run(operation_name=DISCOVER_AND_AUTO_CONFIGURE_TABLES_OPERATION_NAME)
            result = run.result()
        except Exception:
            if run:
                _debug_run_output(application_ctx, run.run_id)
            raise

        assert result.status
        assert result.status.termination_details
        assert result.status.termination_details.type
        assert result.status.termination_details.type.value == TerminationTypeType.SUCCESS.value

        # Verify the TableRecon file was written to the install folder with the canonical name.
        filename = databricks_recon_config.table_recon_filename
        table_recon = application_ctx.installation.load(type_ref=TableRecon, filename=filename)

        # databricks_recon_config points source and target at the same recon_schema, so every table
        # in the schema (the two created by recon_tables) should auto-match by name.
        src_table, tgt_table = recon_tables
        matched_names = {t.source_name for t in table_recon.tables}
        assert src_table.name in matched_names
        assert tgt_table.name in matched_names
