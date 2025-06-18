import logging
import webbrowser

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installation import SerdeError
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.tui import Prompts
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, PermissionDenied

from databricks.labs.lakebridge.config import ReconcileConfig, TableRecon
from databricks.labs.lakebridge.deployment.recon import RECON_JOB_NAME
from databricks.labs.lakebridge.reconcile.recon_config import RECONCILE_OPERATION_NAME

logger = logging.getLogger(__name__)

_RECON_README_URL = "https://github.com/databrickslabs/lakebridge/blob/main/docs/recon_configurations/README.md"


class ReconcileRunner:
    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
        prompts: Prompts,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._prompts = prompts

    def run(self, operation_name=RECONCILE_OPERATION_NAME):
        reconcile_config = self._get_verified_recon_config()
        job_id = self._get_recon_job_id(reconcile_config)
        logger.info(f"Triggering the reconcile job with job_id: `{job_id}`")
        wait = self._ws.jobs.run_now(job_id, job_parameters={"operation_name": operation_name})
        if not wait.run_id:
            raise SystemExit(f"Job {job_id} execution failed. Please check the job logs for more details.")

        job_run_url = f"{self._ws.config.host}/jobs/{job_id}/runs/{wait.run_id}"
        logger.info(
            f"'{operation_name.upper()}' job started. Please check the job_url `{job_run_url}` for the current status."
        )
        if self._prompts.confirm(f"Would you like to open the job run URL `{job_run_url}` in the browser?"):
            webbrowser.open(job_run_url)

    def _get_verified_recon_config(self) -> ReconcileConfig:
        try:
            recon_config = self._installation.load(ReconcileConfig)
        except NotFound as err:
            raise SystemExit("Cannot find existing `reconcile` installation. Please try reinstalling.") from err
        except (PermissionDenied, SerdeError, ValueError, AttributeError) as e:
            install_dir = self._installation.install_folder()
            raise SystemExit(
                f"Existing `reconcile` installation at {install_dir} is corrupted. Please try reinstalling."
            ) from e

        self._verify_recon_table_config(recon_config)
        return recon_config

    def _verify_recon_table_config(self, recon_config):
        source_catalog_or_schema = (
            recon_config.database_config.source_catalog
            if recon_config.database_config.source_catalog
            else recon_config.database_config.source_schema
        )
        # Filename pattern for recon table config `recon_config_<SOURCE>_<CATALOG_OR_SCHEMA>_<FILTER_TYPE>.json`
        # Example: recon_config_snowflake_sample_data_all.json
        filename = f"recon_config_{recon_config.data_source}_{source_catalog_or_schema}_{recon_config.report_type}.json"
        try:
            logger.debug(f"Loading recon table config `{filename}` from workspace.")
            self._installation.load(TableRecon, filename=filename)
        except NotFound as e:
            err_msg = (
                "Cannot find recon table configuration in existing `reconcile` installation. "
                f"Please provide the configuration file {filename} in the workspace."
            )
            logger.error(f"{err_msg}. For more details, please refer to {_RECON_README_URL}")
            raise SystemExit(err_msg) from e
        except (PermissionDenied, SerdeError, ValueError, AttributeError) as e:
            install_dir = self._installation.install_folder()
            err_msg = (
                f"Cannot load corrupted recon table configuration from {install_dir}/{filename}. "
                f"Please validate the file."
            )
            logger.error(f"{err_msg}. For more details, please refer to {_RECON_README_URL}")
            raise SystemExit(err_msg) from e

    def _get_recon_job_id(self, reconcile_config: ReconcileConfig) -> int:
        if reconcile_config.job_id:
            logger.debug("Reconcile job id found in the reconcile config.")
            return int(reconcile_config.job_id)
        if RECON_JOB_NAME in self._install_state.jobs:
            logger.debug("Reconcile job id found in the install state.")
            return int(self._install_state.jobs[RECON_JOB_NAME])
        raise SystemExit("Reconcile Job ID not found. Please try reinstalling.")
