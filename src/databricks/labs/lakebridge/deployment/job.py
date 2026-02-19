import logging
from typing import Any

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue
from databricks.sdk.service import compute
from databricks.sdk.service.jobs import (
    Task,
    PythonWheelTask,
    JobCluster,
    JobSettings,
    JobParameterDefinition,
)
from databricks.labs.lakebridge.config import ReconcileConfig
from databricks.labs.lakebridge.reconcile.constants import ReconSourceType

logger = logging.getLogger(__name__)


class JobDeployment:

    DEFAULT_CLUSTER_NAME = "Remorph_Reconciliation_Cluster"

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

        return {
            "name": self._name_with_prefix(job_name),
            "tags": tags,
            "job_clusters": [] if recon_config.job_overrides else [self._default_job_cluster()],
            "tasks": [
                self._job_recon_task(
                    task_key,
                    description,
                    recon_config,
                    lakebridge_wheel_path,
                ),
            ],
            "max_concurrent_runs": 2,
            "parameters": [JobParameterDefinition(name="operation_name", default="reconcile")],
        }

    def _job_recon_task(
        self, task_key: str, description: str, recon_config: ReconcileConfig, lakebridge_wheel_path: str
    ) -> Task:
        libraries = [
            compute.Library(whl=lakebridge_wheel_path),
        ]

        if recon_config.data_source == ReconSourceType.ORACLE.value:
            # TODO: Automatically fetch a version list for `ojdbc8`
            oracle_driver_version = "23.4.0.24.05"
            libraries.append(
                compute.Library(
                    maven=compute.MavenLibrary(f"com.oracle.database.jdbc:ojdbc8:{oracle_driver_version}"),
                ),
            )

        task = Task(
            task_key=task_key,
            description=description,
            job_cluster_key=None if recon_config.job_overrides else self.DEFAULT_CLUSTER_NAME,
            existing_cluster_id=(
                recon_config.job_overrides.existing_cluster_id if recon_config.job_overrides else None
            ),
            libraries=libraries,
            python_wheel_task=PythonWheelTask(
                package_name=self.parse_package_name(lakebridge_wheel_path),
                entry_point="reconcile",
                parameters=["{{job.parameters.[operation_name]}}"],
            ),
        )
        logger.debug(
            f"Reconciliation job task cluster: existing: {task.existing_cluster_id} or name: {task.job_cluster_key}"
        )
        return task

    def _default_job_cluster(self) -> JobCluster:
        latest_lts_spark = self._ws.clusters.select_spark_version(latest=True, long_term_support=True)
        return JobCluster(
            job_cluster_key=self.DEFAULT_CLUSTER_NAME,
            new_cluster=compute.ClusterSpec(
                data_security_mode=compute.DataSecurityMode.USER_ISOLATION,
                spark_conf={},
                node_type_id=self._get_default_node_type_id(),
                autoscale=compute.AutoScale(min_workers=2, max_workers=10),
                spark_version=latest_lts_spark,
            ),
        )

    def _get_default_node_type_id(self) -> str:
        return self._ws.clusters.select_node_type(local_disk=True, min_memory_gb=16)

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

    def deploy_profiler_ingestion_job(
        self,
        name: str,
        catalog_name: str,
        schema_name: str,
        volume_location: str,
        source_tech: str,
        lakebridge_wheel_path: str,
    ):
        logger.info("Deploying profiler ingestion job.")
        job_id = self._update_or_create_profiler_ingestion_job(
            name, catalog_name, schema_name, volume_location, source_tech, lakebridge_wheel_path
        )
        logger.info(f"Profiler ingestion job deployed with job_id={job_id}")
        logger.info(f"Job URL: {self._ws.config.host}#job/{job_id}")
        self._install_state.save()

    def _update_or_create_profiler_ingestion_job(
        self,
        name: str,
        catalog_name: str,
        schema_name: str,
        volume_location: str,
        source_tech: str,
        lakebridge_wheel_path: str,
    ) -> str:
        description = "Ingest Lakebridge profiler results"
        task_key = "ingest_profiler_extract"

        job_settings = self._profiler_ingestion_job_settings(
            name, task_key, description, catalog_name, schema_name, volume_location, source_tech, lakebridge_wheel_path
        )
        if name in self._install_state.jobs:
            try:
                job_id = int(self._install_state.jobs[name])
                logger.info(f"Updating configuration for job `{name}`, job_id={job_id}")
                self._ws.jobs.reset(job_id, JobSettings(**job_settings))
                return str(job_id)
            except InvalidParameterValue:
                del self._install_state.jobs[name]
                logger.warning(f"Job `{name}` does not exist anymore for some reason")
                return self._update_or_create_profiler_ingestion_job(
                    name, catalog_name, schema_name, volume_location, source_tech, lakebridge_wheel_path
                )

        logger.info(f"Creating new job configuration for job `{name}`")
        new_job = self._ws.jobs.create(**job_settings)
        assert new_job.job_id is not None
        self._install_state.jobs[name] = str(new_job.job_id)
        return str(new_job.job_id)

    def _profiler_ingestion_job_settings(
        self,
        job_name: str,
        task_key: str,
        description: str,
        catalog_name: str,
        schema_name: str,
        volume_location: str,
        source_tech: str,
        lakebridge_wheel_path: str,
    ) -> dict[str, Any]:

        latest_lts_spark = self._ws.clusters.select_spark_version(latest=True, long_term_support=True)
        version = self._product_info.version()
        version = version if not self._ws.config.is_gcp else version.replace("+", "-")
        tags = {"version": f"v{version}"}

        return {
            "name": self._name_with_prefix(job_name),
            "tags": tags,
            "job_clusters": [
                JobCluster(
                    job_cluster_key="Lakebridge_Profiler_Ingest_Cluster",
                    new_cluster=compute.ClusterSpec(
                        data_security_mode=compute.DataSecurityMode.USER_ISOLATION,
                        spark_conf={},
                        node_type_id=self._get_default_node_type_id(),
                        autoscale=compute.AutoScale(min_workers=1, max_workers=3),
                        spark_version=latest_lts_spark,
                    ),
                )
            ],
            "tasks": [
                self._job_profiler_ingestion_task(
                    task_key,
                    description,
                    lakebridge_wheel_path,
                ),
            ],
            "max_concurrent_runs": 1,
            "parameters": [
                JobParameterDefinition(name="catalog_name", default=catalog_name),
                JobParameterDefinition(name="schema_name", default=schema_name),
                JobParameterDefinition(name="volume_path", default=volume_location),
                JobParameterDefinition(name="source_tech", default=source_tech),
            ],
        }

    def _job_profiler_ingestion_task(self, task_key: str, description: str, lakebridge_wheel_path: str) -> Task:
        libraries = [
            compute.Library(whl=lakebridge_wheel_path),
            compute.Library(pypi=compute.PythonPyPiLibrary(package="duckdb")),
        ]

        return Task(
            task_key=task_key,
            description=description,
            job_cluster_key="Lakebridge_Profiler_Ingest_Cluster",
            libraries=libraries,
            python_wheel_task=PythonWheelTask(
                package_name=self.parse_package_name(lakebridge_wheel_path),
                entry_point="profiler_dashboards",
                parameters=["{{job.parameters.[operation_name]}}"],
            ),
        )
