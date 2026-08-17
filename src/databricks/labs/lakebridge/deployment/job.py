import logging
from typing import Any

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service import compute
from databricks.sdk.service.jobs import (
    JobEnvironment,
    JobParameterDefinition,
    JobSettings,
    PythonWheelTask,
    Task,
)

from databricks.labs.lakebridge.config import ReconcileConfig

logger = logging.getLogger(__name__)


class JobDeployment:

    SERVERLESS_ENVIRONMENT_KEY = "reconcile_serverless"
    SERVERLESS_ENVIRONMENT_VERSION = "3"

    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
        product_info: ProductInfo,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._product_info = product_info

    def deploy_recon_job(self, name, recon_config: ReconcileConfig, lakebridge_wheel_path: str):
        logger.info("Deploying reconciliation job.")
        job_id = self._update_or_create_recon_job(name, recon_config, lakebridge_wheel_path)
        logger.info(f"Reconciliation job deployed with job_id={job_id}")
        logger.info(f"Job URL: {self._ws.config.host}#job/{job_id}")
        self._install_state.save()

    def _update_or_create_recon_job(self, name, recon_config: ReconcileConfig, lakebridge_wheel_path: str) -> str:
        description = "Run the reconciliation process"
        task_key = "run_reconciliation"

        job_settings = self._recon_job_settings(name, task_key, description, recon_config, lakebridge_wheel_path)
        if name in self._install_state.jobs:
            try:
                job_id = int(self._install_state.jobs[name])
                logger.info(f"Updating configuration for job `{name}`, job_id={job_id}")
                self._ws.jobs.reset(job_id, JobSettings(**job_settings))
                return str(job_id)
            except InvalidParameterValue:
                del self._install_state.jobs[name]
                logger.warning(f"Job `{name}` does not exist anymore for some reason")
                return self._update_or_create_recon_job(name, recon_config, lakebridge_wheel_path)

        logger.info(f"Creating new job configuration for job `{name}`")
        new_job = self._ws.jobs.create(**job_settings)
        assert new_job.job_id is not None
        self._install_state.jobs[name] = str(new_job.job_id)
        return str(new_job.job_id)

    def _recon_job_settings(
        self,
        job_name: str,
        task_key: str,
        description: str,
        recon_config: ReconcileConfig,
        lakebridge_wheel_path: str,
    ) -> dict[str, Any]:
        version = self._product_info.version()
        version = version if not self._ws.config.is_gcp else version.replace("+", "-")
        tags = {"version": f"v{version}"}
        if recon_config.job_overrides:
            logger.debug(f"Applying deployment overrides: {recon_config.job_overrides}")
            tags.update(recon_config.job_overrides.tags)

        job_settings = {
            "name": self._name_with_prefix(job_name),
            "tags": tags,
            "tasks": [
                self._job_recon_task(
                    task_key,
                    description,
                    recon_config,
                    lakebridge_wheel_path,
                ),
            ],
            "max_concurrent_runs": 2,
            "parameters": [
                JobParameterDefinition(name="operation_name", default="reconcile"),
                JobParameterDefinition(name="install_folder", default=self._installation.install_folder()),
            ],
        }
        if not self._existing_cluster_id(recon_config):
            job_settings["environments"] = [
                JobEnvironment(
                    environment_key=self.SERVERLESS_ENVIRONMENT_KEY,
                    spec=compute.Environment(
                        environment_version=self.SERVERLESS_ENVIRONMENT_VERSION,
                        dependencies=[lakebridge_wheel_path],
                    ),
                )
            ]
        return job_settings

    def _job_recon_task(
        self, task_key: str, description: str, recon_config: ReconcileConfig, lakebridge_wheel_path: str
    ) -> Task:
        existing_cluster_id = self._existing_cluster_id(recon_config)
        # The job runs on serverless compute unless job_overrides points at a classic
        # cluster. Serverless tasks must not set libraries or cluster references; the
        # wheel is supplied through the job-level environment instead
        # (see _recon_job_settings). Classic clusters get the wheel as a library.
        libraries = [compute.Library(whl=lakebridge_wheel_path)] if existing_cluster_id else None

        task = Task(
            task_key=task_key,
            description=description,
            existing_cluster_id=existing_cluster_id,
            environment_key=None if existing_cluster_id else self.SERVERLESS_ENVIRONMENT_KEY,
            libraries=libraries,
            python_wheel_task=PythonWheelTask(
                package_name=self.parse_package_name(lakebridge_wheel_path),
                entry_point="reconcile",
                parameters=["{{job.parameters.[operation_name]}}", "{{job.parameters.[install_folder]}}"],
            ),
        )
        logger.debug(
            f"Reconciliation job task cluster: existing: {task.existing_cluster_id} "
            f"or environment: {task.environment_key}"
        )
        return task

    @staticmethod
    def _existing_cluster_id(recon_config: ReconcileConfig) -> str | None:
        if recon_config.job_overrides:
            return recon_config.job_overrides.existing_cluster_id or None
        return None

    def _name_with_prefix(self, name: str) -> str:
        prefix = self._installation.product()
        return f"{prefix.upper()}_{name}".replace(" ", "_")

    def parse_package_name(self, wheel_path: str) -> str:
        default_name = "databricks_labs_lakebridge"

        name = wheel_path.split("/")[-1].split("-")[0]

        if self._product_info.product_name() not in name:
            logger.warning(f"Parsed package name {name} does not match product name, using default.")
            name = default_name

        return name
