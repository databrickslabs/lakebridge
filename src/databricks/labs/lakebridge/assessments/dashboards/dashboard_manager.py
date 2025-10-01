import os
import json

import logging
from pathlib import Path

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

    DASHBOARD_NAME = "Lakebridge Profiler Assessment"

    def __init__(self, ws: WorkspaceClient, current_user: User, is_debug: bool = False):
        self._ws = ws
        self._current_user = current_user
        self._dashboard_location = f"/Workspace/Users/{self._current_user}/Lakebridge/Dashboards"
        self._is_debug = is_debug

    def create_profiler_summary_dashboard(
        self,
        extract_file: str | None,
        source_tech: str | None,
        catalog_name: str = "lakebridge_profiler",
        schema_name: str = "synapse_runs",
    ) -> None:
        logger.info("Deploying profiler summary dashboard.")

        # Load the AI/BI Dashboard template for the source system
        template_folder = (
            find_project_root(__file__)
            / f"src/databricks/labs/lakebridge/resources/assessments/dashboards/{source_tech}"
        )
        dashboard_loader = DashboardTemplateLoader(template_folder)
        dashboard_json = dashboard_loader.load(source_system="synapse")
        dashboard_str = json.dumps(dashboard_json)

        dashboard_str = dashboard_str.replace("`PROFILER_CATALOG`", f"`{catalog_name}`")
        dashboard_str = dashboard_str.replace("`PROFILER_SCHEMA`", f"`{schema_name}`")

        # TODO: check if the dashboard exists and unpublish it if it does
        # TODO: create a warehouse ID
        dashboard_ws_location = f"/Workspace/Users/{self._current_user}/Lakebridge/Dashboards/"
        dashboard = Dashboard(
            display_name=self.DASHBOARD_NAME,
            parent_path=dashboard_ws_location,
            warehouse_id=None,
            serialized_dashboard=dashboard_str,
        )
        self._ws.lakeview.create(dashboard=dashboard)
