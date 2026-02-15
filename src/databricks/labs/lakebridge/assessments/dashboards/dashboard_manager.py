import io
import os
import json

import logging
from pathlib import Path

from databricks.sdk.errors import PermissionDenied, NotFound, InternalError
from databricks.sdk.errors.platform import ResourceAlreadyExists, DatabricksError
from databricks.sdk.service.dashboards import Dashboard
from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.installation import Installation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.blueprint.wheels import find_project_root

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DashboardTemplateLoader:
    """
    Class for loading the JSON representation of a Databricks dashboard
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

        filename = f"{source_system.lower()}_dashboard.lvdash.json"
        filepath = os.path.join(self.templates_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Could not find dashboard template matching {source_system}.")
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)


class DashboardManager:
    """
    Class for managing the lifecycle of a profiler dashboard summary, a.k.a. "local dashboards"
    """

    _DASHBOARD_NAME = "Lakebridge Profiler Assessment"

    def __init__(
        self, ws: WorkspaceClient, installation: Installation, install_state: InstallState, is_debug: bool = False
    ):
        self._ws = ws
        self._installation = installation
        self._install_state = install_state
        self._is_debug = is_debug

    @staticmethod
    def _replace_catalog_schema(
        serialized_dashboard: str,
        new_catalog: str,
        new_schema: str,
        old_catalog: str = "`PROFILER_CATALOG`",
        old_schema: str = "`PROFILER_SCHEMA`",
    ):
        """Given a serialized JSON dashboard, replaces all catalog and schema references with the
        provided catalog and schema names."""
        updated_dashboard = serialized_dashboard.replace(old_catalog, f"`{new_catalog}`")
        return updated_dashboard.replace(old_schema, f"`{new_schema}`")

    def _create_or_replace_dashboard(
        self, folder: Path, ws_parent_path: str, dest_catalog: str, dest_schema: str
    ) -> Dashboard:
        """
        Creates or updates a profiler summary dashboard in the current user’s Databricks workspace home.
        Existing dashboards are automatically replaced with the latest dashboard template.
        """

        # Load the dashboard template
        logging.info(f"Loading dashboard template from folder: {folder}")
        dash_reference = f"{folder.stem}".lower()
        dashboard_loader = DashboardTemplateLoader(folder)
        dashboard_json = dashboard_loader.load(source_system="synapse")
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
            logging.info("Dashboard already exists! Removing dashboard from workspace location.")
            dashboard_ws_path = str(Path(ws_parent_path) / f"{self._DASHBOARD_NAME}.lvdash.json")
            self._ws.workspace.delete(dashboard_ws_path)
            dashboard = self._ws.lakeview.create(dashboard=dashboard)
        except DatabricksError as e:
            logging.error(f"Could not create profiler summary dashboard: {e}")

        assert dashboard.dashboard_id is not None
        logging.info(f"Created dashboard '{dashboard.dashboard_id}' in workspace location {ws_parent_path}.")
        self._install_state.dashboards[dash_reference] = dashboard.dashboard_id
        return dashboard

    def create_profiler_summary_dashboard(
        self,
        source_tech: str,
        catalog_name: str = "lakebridge_profiler",
        schema_name: str = "profiler_runs",
    ) -> None:
        """Deploys a profiler summary dashboard to the current Databricks user’s workspace home."""

        logger.info("Deploying profiler summary dashboard.")

        # Load the dashboard template for the source system
        template_folder = (
            find_project_root(__file__)
            / f"src/databricks/labs/lakebridge/resources/assessments/dashboards/{source_tech}"
        )
        logger.info(f"Deploying profiler dashboard from template folder: {template_folder}")
        ws_parent_path = f"{self._installation.install_folder()}/dashboards"
        try:
            self._ws.workspace.mkdirs(ws_parent_path)
        except ResourceAlreadyExists:
            logger.info(f"Workspace parent path already exists for dashboards: {ws_parent_path}")
        self._create_or_replace_dashboard(
            folder=template_folder, ws_parent_path=ws_parent_path, dest_catalog=catalog_name, dest_schema=schema_name
        )

    def upload_duckdb_to_uc_volume(self, local_file_path, volume_path):
        """
        Upload a DuckDB file to Unity Catalog Volume
        Args:
            local_file_path (str): Local path to the DuckDB file
            volume_path (str): Target path in UC Volume (e.g., '/Volumes/catalog/schema/volume/myfile.duckdb')
        Returns:
            bool: True if successful, False otherwise
        """

        # Validate inputs
        if not os.path.exists(local_file_path):
            logger.error(f"Local file not found: {local_file_path}")
            return False

        if not volume_path.startswith('/Volumes/'):
            logger.error("Volume path must start with '/Volumes/'")
            return False

        try:
            with open(local_file_path, 'rb') as f:
                file_bytes = f.read()
                binary_data = io.BytesIO(file_bytes)
                self._ws.files.upload(volume_path, binary_data, overwrite=True)
            logger.info(f"Successfully uploaded {local_file_path} to {volume_path}")
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
