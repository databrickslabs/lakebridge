import logging

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound

from databricks.labs.lakebridge.config import ProfilerDashboardConfig
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardManager
from databricks.labs.lakebridge.deployment.job import JobDeployment

logger = logging.getLogger(__name__)

_PROFILER_DASHBOARD_PREFIX = "Profiler Dashboard"
PROFILER_INGESTION_JOB_NAME = f"{_PROFILER_DASHBOARD_PREFIX} Ingestion Job"


class ProfilerDashboardDeployment:
    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
        product_info: ProductInfo,
        job_deployer: JobDeployment,
        profiler_dashboard_manager: ProfilerDashboardManager,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._product_info = product_info
        self._job_deployer = job_deployer
        self._dashboard_deployer = profiler_dashboard_manager

    def install(self, profiler_dashboard_config: ProfilerDashboardConfig | None, wheel_path: str):
        if not profiler_dashboard_config:
            logger.warning("Profiler Dashboard Config is empty.")
            return
        logger.info("Installing the profiler dashboard components.")
        if not self._upload_profiler_extract(profiler_dashboard_config):
            logger.error("Profiler extract upload failed. Aborting installation.")
            return
        try:
            dashboard_id = self._deploy_dashboards(profiler_dashboard_config)
            self._deploy_jobs(profiler_dashboard_config, wheel_path)
            self._install_state.save()
            self._trigger_ingestion_job(dashboard_id)
            logger.info("Installation of the profiler dashboard components completed successfully.")
        except (RuntimeError, ValueError, InvalidParameterValue, NotFound) as e:
            error_msg = f"Failed to deploy profiler dashboard and ingestion job: {e}"
            logger.error(error_msg)
            raise SystemExit(error_msg) from e

    def _trigger_ingestion_job(self, dashboard_id: str) -> None:
        job_id_str = self._install_state.jobs.get(PROFILER_INGESTION_JOB_NAME)
        if not job_id_str:
            logger.warning("Profiler ingestion job ID not found; skipping auto-trigger.")
            return
        job_id = int(job_id_str)
        wait = self._ws.jobs.run_now(job_id)
        if not wait.run_id:
            logger.warning(f"Could not retrieve run ID for profiler ingestion job {job_id}.")
            return
        job_run_url = f"{self._ws.config.host}/jobs/{job_id}/runs/{wait.run_id}"
        logger.info(f"Triggered profiler ingestion job. Job run URL: {job_run_url}")
        logger.info(
            "It may take a few minutes for the ingestion job to complete and for the dashboard to populate with data."
        )
        logger.info(f"Profiler dashboard URL: {self._ws.config.host}/sql/dashboardsv3/{dashboard_id}")

    def uninstall(self, profiler_dashboard_config: ProfilerDashboardConfig | None):
        if not profiler_dashboard_config:
            logger.warning("Profiler Dashboard Config is empty.")
            return
        logger.info("Uninstalling profiler dashboard components.")
        self._remove_dashboards()
        self._remove_jobs()
        logger.info(
            f"Won't remove profiler extract schema `{profiler_dashboard_config.metadata_config.schema}` "
            f"from catalog `{profiler_dashboard_config.metadata_config.catalog}`. "
            f"Please remove it and the tables inside manually."
        )

    def _upload_profiler_extract(self, profiler_dashboard_config: ProfilerDashboardConfig) -> bool:
        logger.info("Uploading the profiler extract file to UC Volume.")
        return self._dashboard_deployer.upload_duckdb_to_uc_volume(profiler_dashboard_config)

    def _deploy_dashboards(self, profiler_dashboard_config: ProfilerDashboardConfig) -> str:
        logger.info("Deploying the profiler dashboard.")
        return self._dashboard_deployer.deploy(profiler_dashboard_config)

    def _get_dashboards(self) -> list[tuple[str, str]]:
        return list(self._install_state.dashboards.items())

    def _remove_dashboards(self):
        for ref, dashboard_id in self._get_dashboards():
            try:
                logger.info(f"Removing old profiler dashboard: {dashboard_id}.")
                del self._install_state.dashboards[ref]
                self._ws.lakeview.trash(dashboard_id)
            except (InvalidParameterValue, NotFound):
                logger.warning(f"Dashboard '{dashboard_id}' does not exist. Skipping.")
                continue

    def _deploy_jobs(self, profiler_dashboard_config: ProfilerDashboardConfig, lakebridge_wheel_path: str):
        logger.info("Deploying the Lakebridge profiler dashboard ingestion job.")
        self._job_deployer.deploy_profiler_ingestion_job(
            PROFILER_INGESTION_JOB_NAME, profiler_dashboard_config, lakebridge_wheel_path
        )
        for job_name, job_id in self._get_deprecated_jobs():
            try:
                logger.info(f"Removing old profiler ingestion job: {job_id}.")
                del self._install_state.jobs[job_name]
                self._ws.jobs.delete(job_id)
            except (InvalidParameterValue, NotFound):
                logger.warning(f"Could not remove old job {job_name} because it no longer exists. Skipping.")
                continue

    def _get_jobs(self) -> list[tuple[str, int]]:
        return [
            (job_name, int(job_id))
            for job_name, job_id in self._install_state.jobs.items()
            if job_name.startswith(_PROFILER_DASHBOARD_PREFIX)
        ]

    def _get_deprecated_jobs(self) -> list[tuple[str, int]]:
        return [(name, job_id) for name, job_id in self._get_jobs() if name != PROFILER_INGESTION_JOB_NAME]

    def _remove_jobs(self):
        for job_name, job_id in self._get_jobs():
            try:
                logger.info(f"Removing old profiler ingestion job '{job_name}'  (job_id: {job_id}).")
                del self._install_state.jobs[job_name]
                self._ws.jobs.delete(int(job_id))
            except (InvalidParameterValue, NotFound):
                logger.warning(f"Could not remove job '{job_name}' because it no longer exists. Skipping.")
                continue
