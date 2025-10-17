import dataclasses
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service import compute
from databricks.sdk.service.jobs import Task, PythonWheelTask, JobCluster, JobSettings, JobParameterDefinition

from databricks.labs.lakebridge.config import ReconcileConfig
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType

logger = logging.getLogger(__name__)

_TEST_JOBS_PURGE_TIMEOUT = timedelta(hours=1, minutes=15)


class JobDeployment:
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

    def deploy_recon_job(self, name, recon_config: ReconcileConfig, remorph_wheel_path: str):
        logger.info("Deploying reconciliation job.")
        job_id = self._update_or_create_recon_job(name, recon_config, remorph_wheel_path)
        logger.info(f"Reconciliation job deployed with job_id={job_id}")
        logger.info(f"Job URL: {self._ws.config.host}#job/{job_id}")
        self._install_state.save()

    def _update_or_create_recon_job(self, name, recon_config: ReconcileConfig, remorph_wheel_path: str) -> str:
        description = "Run the reconciliation process"
        task_key = "run_reconciliation"

        job_settings = self._recon_job_settings(name, task_key, description, recon_config, remorph_wheel_path)
        if name in self._install_state.jobs:
            try:
                job_id = int(self._install_state.jobs[name])
                logger.info(f"Updating configuration for job `{name}`, job_id={job_id}")
                self._ws.jobs.reset(job_id, JobSettings(**job_settings))
                return str(job_id)
            except InvalidParameterValue:
                del self._install_state.jobs[name]
                logger.warning(f"Job `{name}` does not exist anymore for some reason")
                return self._update_or_create_recon_job(name, recon_config, remorph_wheel_path)

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
        remorph_wheel_path: str,
    ) -> dict[str, Any]:
        latest_lts_spark = self._ws.clusters.select_spark_version(latest=True, long_term_support=True)
        version = self._product_info.version()
        version = version if not self._ws.config.is_gcp else version.replace("+", "-")
        tags = {"version": f"v{version}"}
        if self._is_testing():
            # Add RemoveAfter tag for test job cleanup
            date_to_remove = self._get_test_purge_time()
            tags.update({"RemoveAfter": date_to_remove})

        return {
            "name": self._name_with_prefix(job_name),
            "tags": tags,
            "job_clusters": [
                JobCluster(
                    job_cluster_key="Remorph_Reconciliation_Cluster",
                    new_cluster=compute.ClusterSpec(
                        data_security_mode=compute.DataSecurityMode.USER_ISOLATION,
                        spark_conf={},
                        node_type_id=self._get_default_node_type_id(),
                        autoscale=compute.AutoScale(min_workers=2, max_workers=10),
                        spark_version=latest_lts_spark,
                    ),
                )
            ],
            "tasks": [
                self._job_recon_task(
                    Task(
                        task_key=task_key,
                        description=description,
                        job_cluster_key="Remorph_Reconciliation_Cluster",
                    ),
                    recon_config,
                    remorph_wheel_path,
                ),
            ],
            "max_concurrent_runs": 2,
            "parameters": [JobParameterDefinition(name="operation_name", default="reconcile")],
        }

    def _job_recon_task(self, jobs_task: Task, recon_config: ReconcileConfig, remorph_wheel_path: str) -> Task:
        libraries = [
            compute.Library(whl=remorph_wheel_path),
        ]

        if recon_config.data_source == ReconSourceType.ORACLE.value:
            # TODO: Automatically fetch a version list for `ojdbc8`
            oracle_driver_version = "23.4.0.24.05"
            libraries.append(
                compute.Library(
                    maven=compute.MavenLibrary(f"com.oracle.database.jdbc:ojdbc8:{oracle_driver_version}"),
                ),
            )

        return dataclasses.replace(
            jobs_task,
            libraries=libraries,
            python_wheel_task=PythonWheelTask(
                package_name="databricks_labs_remorph",
                entry_point="reconcile",
                parameters=["{{job.parameters.[operation_name]}}"],
            ),
        )

    def _is_testing(self):
        return self._product_info.product_name() != "remorph"

    @staticmethod
    def _get_test_purge_time() -> str:
        return (datetime.now(timezone.utc) + _TEST_JOBS_PURGE_TIMEOUT).strftime("%Y%m%d%H")

    def _get_default_node_type_id(self) -> str:
        return self._ws.clusters.select_node_type(local_disk=True, min_memory_gb=16)

    def _name_with_prefix(self, name: str) -> str:
        prefix = self._installation.product()
        return f"{prefix.upper()}_{name}".replace(" ", "_")
