import logging

from databricks.labs.blueprint.installer import InstallState
from databricks.sdk import WorkspaceClient
from databricks.sdk.service._internal import Wait
from databricks.sdk.service.jobs import Run

from databricks.labs.lakebridge.deployment.recon import RECON_JOB_NAME
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME

logger = logging.getLogger(__name__)

_RECON_DOCS_URL = "https://databrickslabs.github.io/lakebridge/docs/reconcile/"


class ReconcileRunner:
    def __init__(
        self,
        ws: WorkspaceClient,
        install_state: InstallState,
    ):
        self._ws = ws
        self._install_state = install_state

    def run(self, operation_name: str = RECONCILE_OPERATION_NAME) -> tuple[Wait[Run], str]:
        job_id = self._get_recon_job_id()
        logger.info(f"Triggering the reconcile job with job_id: `{job_id}`")
        wait = self._ws.jobs.run_now(job_id, job_parameters={"operation_name": operation_name})
        if not wait.run_id:
            raise SystemExit(f"Job {job_id} execution failed. Please check the job logs for more details.")

        job_run_url = f"{self._ws.config.host}/jobs/{job_id}/runs/{wait.run_id}"
        logger.info(
            f"'{operation_name.upper()}' job started. Please check the job_url `{job_run_url}` for the current status."
        )
        return wait, job_run_url

    def _get_recon_job_id(self) -> int:
        if RECON_JOB_NAME in self._install_state.jobs:
            logger.debug("Reconcile job id found in the install state.")
            return int(self._install_state.jobs[RECON_JOB_NAME])
        raise SystemExit("Reconcile Job ID not found. Please try reinstalling.")
