import importlib.resources
import logging
from collections.abc import Generator, Sequence
from importlib.abc import Traversable
from pathlib import PurePosixPath
from typing import Any

from databricks.labs import switch
from databricks.labs.switch.__about__ import __version__ as switch_version
from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.paths import WorkspacePath
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound
from databricks.sdk.service import compute
from databricks.sdk.service.jobs import JobCluster, JobParameterDefinition, JobSettings, NotebookTask, Source, Task


logger = logging.getLogger(__name__)


class SwitchDeployment:
    _INSTALL_STATE_KEY = "Switch"
    _TRANSPILER_ID = "switch"

    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state

    def install(self, use_serverless: bool = True) -> None:
        """Deploy Switch to workspace and configure resources."""
        logger.debug("Deploying Switch resources to workspace...")
        try:
            self._deploy_resources_to_workspace()
            self._setup_job(use_serverless)
            logger.debug("Switch deployment completed")
        except (RuntimeError, ValueError, InvalidParameterValue) as e:
            msg = f"Failed to setup required resources for Switch llm transpiler: {e}"
            logger.error(msg)
            raise SystemExit(msg) from e

    def uninstall(self) -> None:
        """Remove Switch job from workspace."""
        if self._INSTALL_STATE_KEY not in self._install_state.jobs:
            logger.debug("No Switch job found in InstallState")
            return

        job_id = int(self._install_state.jobs[self._INSTALL_STATE_KEY])
        try:
            logger.info(f"Removing Switch job: id={job_id}")
            del self._install_state.jobs[self._INSTALL_STATE_KEY]
            self._ws.jobs.delete(job_id)
            self._install_state.save()
        except (InvalidParameterValue, NotFound):
            logger.debug(f"Switch job (id={job_id}) doesn't exist anymore, nothing to do.")
            self._install_state.save()

    def _get_switch_workspace_path(self) -> WorkspacePath:
        installation_root = self._installation.install_folder()
        return WorkspacePath(self._ws, installation_root) / "switch"

    def _deploy_resources_to_workspace(self) -> None:
        """Replicate the Switch package sources to the workspace."""
        # TODO: This is temporary, instead the jobs should directly run the code from the deployed wheel/package.
        resource_root = self._get_switch_workspace_path()
        # Replace existing resources, to avoid stale files and potential confusion.
        if resource_root.exists():
            resource_root.rmdir(recursive=True)
        resource_root.mkdir(parents=True)
        already_created = {resource_root}
        logger.info(f"Copying resources to workspace: {resource_root}")
        for resource_path, resource in self._enumerate_package_files(switch):
            # Resource path has a leading 'switch' that we want to strip off.
            nested_path = resource_path.relative_to(PurePosixPath("switch"))
            upload_path = resource_root / nested_path
            if (parent := upload_path.parent) not in already_created:
                logger.debug(f"Creating workspace directory: {parent}")
                parent.mkdir()
                already_created.add(parent)
            logger.debug(f"Uploading: {resource_path} -> {upload_path}")
            upload_path.write_bytes(resource.read_bytes())
        logger.debug(f"Resources copied to workspace: {resource_root}")

    @staticmethod
    def _enumerate_package_files(package) -> Generator[tuple[PurePosixPath, Traversable]]:
        # Locate the root of the package, and then enumerate all its files recursively.
        root = importlib.resources.files(package)

        def _enumerate_resources(
            resource: Traversable, parent: PurePosixPath = PurePosixPath(".")
        ) -> Generator[tuple[PurePosixPath, Traversable]]:
            if resource.name.startswith("."):
                # Skip hidden files and directories
                return
            if resource.is_dir():
                next_parent = parent / resource.name
                for child in resource.iterdir():
                    yield from _enumerate_resources(child, next_parent)
            elif resource.is_file():
                # Skip compiled Python files
                if not (name := resource.name).endswith((".pyc", ".pyo")):
                    yield parent / name, resource

        yield from _enumerate_resources(root)

    def _setup_job(self, use_serverless: bool = True) -> None:
        """Create or update Switch job."""
        existing_job_id = self._get_existing_job_id()
        logger.debug("Setting up Switch job in workspace...")
        job_id = self._create_or_update_switch_job(existing_job_id, use_serverless)
        self._install_state.jobs[self._INSTALL_STATE_KEY] = job_id
        self._install_state.save()
        job_url = f"{self._ws.config.host}/jobs/{job_id}"
        logger.info(f"Switch job created/updated: {job_url}")

    def _get_existing_job_id(self) -> str | None:
        """Check if Switch job already exists in workspace."""
        if self._INSTALL_STATE_KEY not in self._install_state.jobs:
            return None
        try:
            job_id = self._install_state.jobs[self._INSTALL_STATE_KEY]
            self._ws.jobs.get(int(job_id))
            return job_id
        except (InvalidParameterValue, NotFound, ValueError):
            return None

    def _create_or_update_switch_job(self, job_id: str | None, use_serverless: bool = True) -> str:
        """Create or update Switch job, returning job ID."""
        job_settings = self._get_switch_job_settings(use_serverless)

        # Try to update existing job
        if job_id:
            try:
                logger.debug(f"Updating Switch job (id={job_id}) with settings: {job_settings}")
                self._ws.jobs.reset(int(job_id), JobSettings(**job_settings))
                return job_id
            except (ValueError, InvalidParameterValue):
                logger.warning("Previous Switch job not found, creating new one")

        # Create new job
        logger.debug(f"Creating new Switch job with settings: {job_settings}")
        new_job = self._ws.jobs.create(**job_settings)
        new_job_id = str(new_job.job_id)
        assert new_job_id is not None
        return new_job_id

    def _get_switch_job_settings(self, use_serverless: bool = True) -> dict[str, Any]:
        """Build job settings for Switch transpiler."""
        job_name = "Lakebridge_Switch"
        notebook_path = self._get_switch_workspace_path() / "notebooks" / "00_main"

        task = Task(
            task_key="run_transpilation",
            notebook_task=NotebookTask(notebook_path=str(notebook_path), source=Source.WORKSPACE),
            disable_auto_optimization=True,  # To disable retries on failure
        )

        settings: dict[str, Any] = {
            "name": job_name,
            "tags": {"created_by": self._ws.current_user.me().user_name, "switch_version": f"v{switch_version}"},
            "tasks": [task],
            "parameters": self._generate_switch_job_parameters(),
            "max_concurrent_runs": 100,  # Allow simultaneous transpilations
        }

        if not use_serverless:
            job_cluster_key = "Switch_Cluster"
            settings["job_clusters"] = [
                JobCluster(
                    job_cluster_key=job_cluster_key,
                    new_cluster=compute.ClusterSpec(
                        spark_version=self._ws.clusters.select_spark_version(latest=True, long_term_support=True),
                        node_type_id=self._ws.clusters.select_node_type(local_disk=True, min_memory_gb=16),
                        num_workers=1,
                        data_security_mode=compute.DataSecurityMode.USER_ISOLATION,
                    ),
                )
            ]
            task.job_cluster_key = job_cluster_key

        return settings

    @staticmethod
    def _generate_switch_job_parameters() -> Sequence[JobParameterDefinition]:
        # Add required runtime parameters, static for now.
        parameters = {
            "source_tech": "",
            "input_dir": "",
            "output_dir": "",
            "foundation_model": "databricks-claude-sonnet-4-5",
            "catalog": "lakebridge",
            "schema": "switch",
        }
        return [JobParameterDefinition(name=key, default=value) for key, value in parameters.items()]
