import logging
from importlib.resources import files

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.labs.blueprint.wheels import find_project_root
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound

import databricks.labs.lakebridge.resources
from databricks.labs.lakebridge.config import ReconcileConfig
from databricks.labs.lakebridge.deployment.dashboard import DashboardDeployment
from databricks.labs.lakebridge.deployment.job import JobDeployment
from databricks.labs.lakebridge.deployment.table import TableDeployment

logger = logging.getLogger(__name__)

_RECON_PREFIX = "Reconciliation"
RECON_JOB_NAME = f"{_RECON_PREFIX} Runner"


class ReconDeployment:
    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
        product_info: ProductInfo,
        table_deployer: TableDeployment,
        job_deployer: JobDeployment,
        dashboard_deployer: DashboardDeployment,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._product_info = product_info
        self._table_deployer = table_deployer
        self._job_deployer = job_deployer
        self._dashboard_deployer = dashboard_deployer

    def install(self, recon_config: ReconcileConfig | None, wheel_paths: list[str]):
        if not recon_config:
            logger.warning("Recon Config is empty.")
            return
        logger.info("Installing reconcile components.")
        self._deploy_tables(recon_config)
        self._deploy_dashboards(recon_config)
        # TODO INVESTIGATE: Why is this needed?
        remorph_wheel_path = [whl for whl in wheel_paths if "lakebridge" in whl][0]
        self._deploy_jobs(recon_config, remorph_wheel_path)
        self._install_state.save()
        logger.info("Installation of reconcile components completed successfully.")

    def uninstall(self, recon_config: ReconcileConfig | None):
        if not recon_config:
            return
        logger.info("Uninstalling reconcile components.")
        self._remove_dashboards()
        self._remove_jobs()
        logging.info(
            f"Won't remove reconcile metadata schema `{recon_config.metadata_config.schema}` "
            f"from catalog `{recon_config.metadata_config.catalog}`. Please remove it and the tables inside manually."
        )
        logging.info(
            f"Won't remove configured reconcile secret scope `{recon_config.secret_scope}`. "
            f"Please remove it manually."
        )

    def _deploy_tables(self, recon_config: ReconcileConfig):
        logger.info("Deploying reconciliation metadata tables.")
        catalog = recon_config.metadata_config.catalog
        schema = recon_config.metadata_config.schema
        resources = files(databricks.labs.lakebridge.resources)
        query_dir = resources.joinpath("reconcile/queries/installation")

        sqls_to_deploy = [
            "main.sql",
            "metrics.sql",
            "details.sql",
            "aggregate_metrics.sql",
            "aggregate_details.sql",
            "aggregate_rules.sql",
        ]

        for sql_file in sqls_to_deploy:
            table_sql_file = query_dir.joinpath(sql_file)
            self._table_deployer.deploy_table_from_ddl_file(catalog, schema, sql_file.strip(".sql"), table_sql_file)

    def _deploy_dashboards(self, recon_config: ReconcileConfig):
        logger.info("Deploying reconciliation dashboards.")
        dashboard_base_dir = (
            find_project_root(__file__) / "src/databricks/labs/lakebridge/resources/reconcile/dashboards"
        )
        self._dashboard_deployer.deploy(dashboard_base_dir, recon_config)

    def _get_dashboards(self) -> list[tuple[str, str]]:
        return list(self._install_state.dashboards.items())

    def _remove_dashboards(self):
        logger.info("Removing reconciliation dashboards.")
        for dashboard_ref, dashboard_id in self._get_dashboards():
            try:
                logger.info(f"Removing dashboard with id={dashboard_id}.")
                del self._install_state.dashboards[dashboard_ref]
                self._ws.lakeview.trash(dashboard_id)
            except (InvalidParameterValue, NotFound):
                logger.warning(f"Dashboard with id={dashboard_id} doesn't exist anymore for some reason.")
                continue

    def _deploy_jobs(self, recon_config: ReconcileConfig, remorph_wheel_path: str):
        logger.info("Deploying reconciliation jobs.")
        self._job_deployer.deploy_recon_job(RECON_JOB_NAME, recon_config, remorph_wheel_path)
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
            if job_name.startswith(_RECON_PREFIX)
        ]

    def _get_deprecated_jobs(self) -> list[tuple[str, int]]:
        return [
            (job_name, int(job_id))
            for job_name, job_id in self._install_state.jobs.items()
            if job_name.startswith(_RECON_PREFIX) and job_name != RECON_JOB_NAME
        ]

    def _remove_jobs(self):
        logger.info("Removing Reconciliation Jobs.")
        for job_name, job_id in self._get_jobs():
            try:
                logger.info(f"Removing job {job_name} with job_id={job_id}.")
                del self._install_state.jobs[job_name]
                self._ws.jobs.delete(int(job_id))
            except (InvalidParameterValue, NotFound):
                logger.warning(f"{job_name} doesn't exist anymore for some reason.")
                continue
