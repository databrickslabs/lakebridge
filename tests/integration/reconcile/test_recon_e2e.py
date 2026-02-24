import re
import logging

from databricks.sdk.service.jobs import TerminationTypeType
from databricks.sdk.core import DatabricksError

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME
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


def test_recon_databricks_job_succeeds(
    application_ctx: ApplicationContext,
    databricks_recon_config: ReconcileConfig,
    databricks_recon_table_config: TableRecon,
) -> None:
    with generate_recon_application_context(
        application_ctx, databricks_recon_config, databricks_recon_table_config
    ) as app_ctx:
        _run_recon_e2e_spec(app_ctx)


def test_recon_sql_server_job_succeeds(
    application_ctx: ApplicationContext, tsql_recon_config: ReconcileConfig, tsql_recon_table_config: TableRecon
) -> None:
    with generate_recon_application_context(application_ctx, tsql_recon_config, tsql_recon_table_config) as app_ctx:
        _run_recon_e2e_spec(app_ctx)
