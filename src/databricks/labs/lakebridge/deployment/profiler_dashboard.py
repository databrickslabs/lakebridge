import logging

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound

from databricks.labs.lakebridge.config import ProfilerDashboardConfig
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardManager
from databricks.labs.lakebridge.deployment.job import JobDeployment
from databricks.labs.lakebridge.deployment.table import TableDeployment

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
        table_deployer: TableDeployment,
        job_deployer: JobDeployment,
        dashboard_deployer: ProfilerDashboardManager,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._product_info = product_info
        self._table_deployer = table_deployer
        self._job_deployer = job_deployer
        self._dashboard_deployer = dashboard_deployer

    def install(self, profiler_dashboard_config: ProfilerDashboardConfig | None, wheel_path: str):
        if not profiler_dashboard_config:
            logger.warning("Profiler Dashboard Config is empty.")
            return
        logger.info("Installing the profiler dashboard components.")
        self._deploy_dashboards(profiler_dashboard_config)
        self._deploy_jobs(profiler_dashboard_config, wheel_path)
        self._install_state.save()
        logger.info("Installation of the profiler dashboard components completed successfully.")

    def uninstall(self, profiler_dashboard_config: ProfilerDashboardConfig | None):
        if not profiler_dashboard_config:
            logger.warning("Profiler Dashboard Config is empty.")
            return
        logger.info("Uninstalling profiler dashboard components.")
        self._remove_dashboards()
        self._remove_jobs()
        logging.info(
            f"Won't remove profiler extract schema `{profiler_dashboard_config.metadata_config.schema}` "
            f"from catalog `{profiler_dashboard_config.metadata_config.catalog}`. "
            f"Please remove it and the tables inside manually."
        )

    def _deploy_dashboards(self, profiler_dashboard_config: ProfilerDashboardConfig):
        logger.info("Deploying the profiler dashboard.")
        self._dashboard_deployer.deploy(profiler_dashboard_config)

    def _get_dashboards(self) -> list[tuple[str, str]]:
        return list(self._install_state.dashboards.items())

    def _remove_dashboards(self):
        logger.info("Removing profiler dashboard.")
        for dashboard_ref, dashboard_id in self._get_dashboards():
            try:
                logger.info(f"Removing dashboard with id={dashboard_id}.")
                del self._install_state.dashboards[dashboard_ref]
                self._ws.lakeview.trash(dashboard_id)
            except (InvalidParameterValue, NotFound):
                logger.warning(f"Dashboard with id={dashboard_id} doesn't exist anymore for some reason.")
                continue

    def _deploy_jobs(self, profiler_dashboard_config: ProfilerDashboardConfig, lakebridge_wheel_path: str):
        logger.info("Deploying the Lakebridge profiler dashboard ingestion job.")
        self._job_deployer.deploy_profiler_ingestion_job(
            PROFILER_INGESTION_JOB_NAME, profiler_dashboard_config, lakebridge_wheel_path
        )
        for job_name, job_id in self._get_deprecated_jobs():
            try:
                logger.info(f"Removing job_id={job_id}, as it is no longer needed.")
                del self._install_state.jobs[job_name]
                self._ws.jobs.delete(job_id)
            except (InvalidParameterValue, NotFound):
                logger.warning(f"{job_name} doesn't exist anymore for some reason.")
                continue

    def _get_jobs(self) -> list[tuple[str, int]]:
        return [
            (job_name, int(job_id))
            for job_name, job_id in self._install_state.jobs.items()
            if job_name.startswith(_PROFILER_DASHBOARD_PREFIX)
        ]

    def _get_deprecated_jobs(self) -> list[tuple[str, int]]:
        return [
            (job_name, int(job_id))
            for job_name, job_id in self._install_state.jobs.items()
            if job_name.startswith(_PROFILER_DASHBOARD_PREFIX) and job_name != PROFILER_INGESTION_JOB_NAME
        ]

    def _remove_jobs(self):
        logger.info("Removing Profiler Ingestion Job.")
        for job_name, job_id in self._get_jobs():
            try:
                logger.info(f"Removing job {job_name} with job_id={job_id}.")
                del self._install_state.jobs[job_name]
                self._ws.jobs.delete(int(job_id))
            except (InvalidParameterValue, NotFound):
                logger.warning(f"{job_name} doesn't exist anymore for some reason.")
                continue
