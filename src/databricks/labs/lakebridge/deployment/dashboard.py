import os
import io
import json
import logging
from datetime import timedelta
from pathlib import Path

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.lakebridge.assessments._constants import PRODUCT_PATH_PREFIX
from databricks.labs.lsql.dashboards import DashboardMetadata, Dashboards
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import InvalidParameterValue, NotFound, DeadlineExceeded, InternalError, PermissionDenied
from databricks.sdk.retries import retried
from databricks.sdk.service.dashboards import LifecycleState, Dashboard
from databricks.sdk.errors.platform import ResourceAlreadyExists, DatabricksError
from databricks.labs.lakebridge.config import ReconcileMetadataConfig, ProfilerDashboardConfig

logger = logging.getLogger(__name__)


class DashboardDeployment:

    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state

    def deploy(
        self,
        folder: Path,
        metadata_config: ReconcileMetadataConfig,
    ):
        """
        Create dashboards from Dashboard metadata files.
        The given folder is expected to contain subfolders each containing metadata for individual dashboards.

        :param folder: Path to the base folder.
        :param metadata_config: Meta configuration for reconciliation.
        """
        logger.info(f"Deploying dashboards from base folder {folder}")
        parent_path = f"{self._installation.install_folder()}/dashboards"
        try:
            self._ws.workspace.mkdirs(parent_path)
        except ResourceAlreadyExists:
            logger.info(f"Dashboard parent path already exists: {parent_path}")

        valid_dashboard_refs = set()
        for dashboard_folder in folder.iterdir():
            # Make sure the directory contains a dashboard
            if not (dashboard_folder.is_dir() and dashboard_folder.joinpath("dashboard.yml").exists()):
                continue
            valid_dashboard_refs.add(self._dashboard_reference(dashboard_folder))
            dashboard = self._update_or_create_dashboard(dashboard_folder, parent_path, metadata_config)
            logger.info(
                f"Dashboard deployed with URL: {self._ws.config.host}/sql/dashboardsv3/{dashboard.dashboard_id}"
            )
            self._install_state.save()

        self._remove_deprecated_dashboards(valid_dashboard_refs)

    def _dashboard_reference(self, folder: Path) -> str:
        return f"{folder.stem}".lower()

    # InternalError and DeadlineExceeded are retried because of Lakeview internal issues
    # These issues have been reported to and are resolved by the Lakeview team
    # Keeping the retry for resilience
    @retried(on=[InternalError, DeadlineExceeded], timeout=timedelta(minutes=3))
    def _update_or_create_dashboard(
        self,
        folder: Path,
        ws_parent_path: str,
        config: ReconcileMetadataConfig,
    ) -> Dashboard:
        logger.info(f"Reading dashboard folder {folder}")
        metadata = DashboardMetadata.from_path(folder).replace_database(
            catalog=config.catalog,
            catalog_to_replace="remorph",
            database=config.schema,
            database_to_replace="reconcile",
        )

        metadata.display_name = self._name_with_prefix(metadata.display_name)
        reference = self._dashboard_reference(folder)
        dashboard_id = self._install_state.dashboards.get(reference)
        if dashboard_id is not None:
            try:
                dashboard_id = self._handle_existing_dashboard(dashboard_id, metadata.display_name)
            except (NotFound, InvalidParameterValue):
                logger.info(f"Recovering invalid dashboard: {metadata.display_name} ({dashboard_id})")
                try:
                    dashboard_path = f"{ws_parent_path}/{metadata.display_name}.lvdash.json"
                    self._ws.workspace.delete(dashboard_path)  # Cannot recreate dashboard if file still exists
                    logger.debug(
                        f"Deleted dangling dashboard {metadata.display_name} ({dashboard_id}): {dashboard_path}"
                    )
                except NotFound:
                    pass
                dashboard_id = None  # Recreate the dashboard if it's reference is corrupted (manually)

        dashboard = Dashboards(self._ws).create_dashboard(
            metadata,
            dashboard_id=dashboard_id,
            parent_path=ws_parent_path,
            warehouse_id=self._ws.config.warehouse_id,
            publish=True,
        )
        assert dashboard.dashboard_id is not None
        self._install_state.dashboards[reference] = dashboard.dashboard_id
        return dashboard

    def _name_with_prefix(self, name: str) -> str:
        prefix = self._installation.product()
        return f"{prefix.upper()}_{name}".replace(" ", "_")

    def _handle_existing_dashboard(self, dashboard_id: str, display_name: str) -> str | None:
        dashboard = self._ws.lakeview.get(dashboard_id)
        if dashboard.lifecycle_state is None:
            raise NotFound(f"Dashboard life cycle state: {display_name} ({dashboard_id})")
        if dashboard.lifecycle_state == LifecycleState.TRASHED:
            logger.info(f"Recreating trashed dashboard: {display_name} ({dashboard_id})")
            return None  # Recreate the dashboard if it is trashed (manually)
        return dashboard_id  # Update the existing dashboard

    def _remove_deprecated_dashboards(self, valid_dashboard_refs: set[str]):
        for ref, dashboard_id in self._install_state.dashboards.items():
            if ref not in valid_dashboard_refs:
                try:
                    logger.info(f"Removing dashboard_id={dashboard_id}, as it is no longer needed.")
                    del self._install_state.dashboards[ref]
                    self._ws.lakeview.trash(dashboard_id)
                except (InvalidParameterValue, NotFound):
                    logger.warning(f"Dashboard `{dashboard_id}` doesn't exist anymore for some reason.")
                    continue


class ProfilerDashboardTemplateLoader:
    """
    Class for loading the JSON representation of a profiler dashboard
    according to the source system.
    """

    def __init__(self, templates_dir: Path | None):
        self.templates_dir = templates_dir

    def load(self, source_system: str) -> dict:
        """
        Loads a profiler summary dashboard.
        :param source_system: - the name of the source data warehouse
        """
        if self.templates_dir is None:
            raise ValueError("Dashboard template path cannot be empty.")

        filename = f"lakebridge_{source_system.lower()}_profiler_summary.lvdash.json"
        filepath = os.path.join(self.templates_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Could not find dashboard template matching {source_system}.") from e


class ProfilerDashboardManager:
    """
    Class for managing the lifecycle of a profiler dashboard summary, a.k.a. "local dashboards"
    """

    _DASHBOARD_NAME = "Lakebridge Profiler Assessment"

    def __init__(
        self,
        ws: WorkspaceClient,
        installation: Installation,
        install_state: InstallState,
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state

    @staticmethod
    def _replace_catalog_schema(
        serialized_dashboard: str,
        new_catalog: str,
        new_schema: str,
        old_catalog: str = "<CATALOG_NAME>",
        old_schema: str = "<SCHEMA_NAME>",
    ):
        """Given a serialized JSON dashboard, replaces all catalog and schema references with the
        provided catalog and schema names."""
        updated_dashboard = serialized_dashboard.replace(old_catalog, f"`{new_catalog}`")
        return updated_dashboard.replace(old_schema, f"`{new_schema}`")

    def _create_or_replace_dashboard(
        self, folder: Path, ws_parent_path: str, dest_catalog: str, dest_schema: str, source_system: str
    ) -> Dashboard:
        """
        Creates or updates a profiler summary dashboard in the current user’s Databricks workspace home.
        Existing dashboards are automatically replaced with the latest dashboard template.
        """

        # Load the dashboard template
        logger.info(f"Loading dashboard template from folder: {folder}")
        dash_reference = f"{folder.stem}".lower()
        dashboard_loader = ProfilerDashboardTemplateLoader(folder)
        dashboard_json = dashboard_loader.load(source_system)
        dashboard_str = json.dumps(dashboard_json)

        # Replace catalog and schema placeholders
        updated_dashboard_str = self._replace_catalog_schema(
            dashboard_str, new_catalog=dest_catalog, new_schema=dest_schema
        )
        dashboard = Dashboard(
            display_name=self._DASHBOARD_NAME,
            parent_path=ws_parent_path,
            warehouse_id=self._ws.config.warehouse_id,
            serialized_dashboard=updated_dashboard_str,
        )

        # Create dashboard or replace if previously deployed
        try:
            dashboard = self._ws.lakeview.create(dashboard=dashboard)
        except ResourceAlreadyExists:
            logger.info("Dashboard already exists! Removing dashboard from workspace location.")
            dashboard_ws_path = str(Path(ws_parent_path) / f"{self._DASHBOARD_NAME}.lvdash.json")
            self._ws.workspace.delete(dashboard_ws_path)
            dashboard = self._ws.lakeview.create(dashboard=dashboard)
        except DatabricksError as e:
            logger.error(f"Could not create profiler summary dashboard: {e}")

        assert dashboard.dashboard_id is not None
        logger.info(f"Created dashboard '{dashboard.dashboard_id}' in workspace location {ws_parent_path}.")
        self._install_state.dashboards[dash_reference] = dashboard.dashboard_id
        return dashboard

    def deploy(self, profiler_dashboard_config: ProfilerDashboardConfig) -> str:
        """Deploys a profiler summary dashboard to the current Databricks user’s workspace home.
        Returns the deployed dashboard ID."""

        logger.info("Deploying profiler summary dashboard.")
        source_tech = profiler_dashboard_config.source_tech
        catalog_name = profiler_dashboard_config.metadata_config.catalog
        schema_name = profiler_dashboard_config.metadata_config.schema
        # Load the dashboard template for the source system
        template_folder = (
            PRODUCT_PATH_PREFIX / f"src/databricks/labs/lakebridge/resources/assessments/dashboards/{source_tech}"
        )
        logger.info(f"Deploying profiler dashboard from template folder: {template_folder}")
        ws_parent_path = f"{self._installation.install_folder()}/dashboards"
        try:
            self._ws.workspace.mkdirs(ws_parent_path)
        except ResourceAlreadyExists:
            logger.info(f"Workspace parent path already exists for dashboards: {ws_parent_path}")
        dashboard = self._create_or_replace_dashboard(
            folder=template_folder,
            ws_parent_path=ws_parent_path,
            dest_catalog=catalog_name,
            dest_schema=schema_name,
            source_system=source_tech,
        )
        if dashboard.dashboard_id is None:
            msg = "Dashboard deployment failed to return dashboard ID"
            logger.error(msg)
            raise ValueError(msg)
        return dashboard.dashboard_id

    @staticmethod
    def resolve_volume_path(local_file_path: str, volume_path: str) -> str:
        """
        Resolves the UC Volume path based on extract file path and UC Volume path inputs from the CLI.
        Args:
            local_file_path (str): Local path to the DuckDB file
            volume_path (str): Target path in UC Volume (e.g., '/Volumes/catalog/schema/volume/myfile.duckdb')
        Returns:
            str: The resolved UC Volume path
        """
        extract_path = Path(local_file_path)
        upload_path = Path(volume_path)

        # Ensure that the extract file path ends with a valid DuckDB file name
        if extract_path.name == "" or extract_path.suffix == "":
            raise ValueError(f"The profile extract path must include a file name: {extract_path}")

        # If the upload file path includes a valid file name, then return it
        # Otherwise, append the extract file name to the UC Volume upload path
        if upload_path.suffix:
            return volume_path

        return str(upload_path / extract_path.name)

    def upload_duckdb_to_uc_volume(self, profiler_dashboard_config: ProfilerDashboardConfig) -> bool:
        """
        Upload a DuckDB file to Unity Catalog Volume
        Args:
            profiler_dashboard_config (ProfilerDashboardConfig): the profiler dashboard config created
              as a result of the user completing the CLI prompts
        Returns:
            bool: True if successful, False otherwise
        """
        local_file_path = profiler_dashboard_config.extract_file_path
        catalog = profiler_dashboard_config.metadata_config.catalog
        schema = profiler_dashboard_config.metadata_config.schema
        volume = profiler_dashboard_config.metadata_config.volume
        volume_path = f"/Volumes/{catalog}/{schema}/{volume}"

        # Validate the extract file path
        if not os.path.exists(local_file_path):
            logger.error(f"Local file not found: {local_file_path}")
            return False

        # Validate the upload Volume path
        resolved_volume_path = self.resolve_volume_path(local_file_path, volume_path)
        if not resolved_volume_path.startswith('/Volumes/'):
            logger.error("Volume path must start with '/Volumes/'")
            return False

        try:
            with open(local_file_path, 'rb') as f:
                file_bytes = f.read()
                binary_data = io.BytesIO(file_bytes)
                self._ws.files.upload(resolved_volume_path, binary_data, overwrite=True)
            logger.info(f"Successfully uploaded {local_file_path} to {resolved_volume_path}")
            return True
        except FileNotFoundError as e:
            logger.error(f"Profiler extract file was not found: \n{e}")
            return False
        except PermissionDenied as e:
            logger.error(f"Insufficient privileges detected while accessing Volume path: \n{e}")
            return False
        except NotFound as e:
            logger.error(f"Invalid Volume path provided: \n{e}")
            return False
        except InternalError as e:
            logger.error(f"Internal Databricks error while uploading extract file: \n{e}")
            return False
