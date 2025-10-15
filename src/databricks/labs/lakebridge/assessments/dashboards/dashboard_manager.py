import os
import json

import logging
from pathlib import Path

from databricks.sdk.errors.platform import ResourceAlreadyExists
from databricks.sdk.service.dashboards import Dashboard
from databricks.sdk.service.iam import User
from databricks.sdk import WorkspaceClient

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
            raise FileNotFoundError(f"Could not find dashboard template matching '{source_system}'.")
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)


class DashboardManager:
    """
    Class for managing the lifecycle of a profiler dashboard summary, a.k.a. "local dashboards"
    """

    _DASHBOARD_NAME = "Lakebridge Profiler Assessment"

    def __init__(self, ws: WorkspaceClient, current_user: User, is_debug: bool = False):
        self._ws = ws
        self._current_user = current_user
        self._dashboard_location = f"/Workspace/Users/{self._current_user}/Lakebridge/Dashboards"
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
        logging.info(f"Loading dashboard template {folder}")
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
            dashboard_full_path = f"{ws_parent_path}{self._DASHBOARD_NAME}.lvdash.json"
            self._ws.workspace.delete(dashboard_full_path)
            dashboard = self._ws.lakeview.create(dashboard=dashboard)
        except Exception as e:
            logging.error(f"Could not create profiler summary dashboard: {e}")

        if dashboard.dashboard_id:
            logging.info(f"Created dashboard '{dashboard.dashboard_id}' in workspace location '{ws_parent_path}'.")

        return dashboard

    def create_profiler_summary_dashboard(
        self,
        extract_file: str | None,
        source_tech: str | None,
        catalog_name: str = "lakebridge_profiler",
        schema_name: str = "profiler_runs",
    ) -> None:
        """Deploys a profiler summary dashboard to the current Databricks user’s workspace home."""

        logger.info("Deploying profiler summary dashboard.")

        # Load the AI/BI Dashboard template for the source system
        template_folder = (
            find_project_root(__file__)
            / f"src/databricks/labs/lakebridge/resources/assessments/dashboards/{source_tech}"
        )
        ws_path = f"/Workspace/Users/{self._current_user}/Lakebridge/Dashboards/"
        self._create_or_replace_dashboard(
            folder=template_folder, ws_parent_path=ws_path, dest_catalog=catalog_name, dest_schema=schema_name
        )
