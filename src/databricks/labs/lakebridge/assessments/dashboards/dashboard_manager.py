import os
import json

import logging
from typing import Dict

from databricks.sdk.service.iam import User
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.deployment.dashboard import DashboardDeployment
from databricks.labs.blueprint.wheels import find_project_root

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DashboardTemplateLoader:
    """
    Class for loading the JSON representation of a Databricks dashboard
    according to the source system.
    """

    def __init__(self, templates_dir: str = "templates"):
        self.templates_dir = templates_dir

    def load(self, source_system: str) -> Dict:
        """
        Loads a profiler summary dashboard.
        :param source_system: - the name of the source data warehouse
        """
        filename = f"{source_system.lower()}_dashboard.json"
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

    def __init__(self, ws: WorkspaceClient, current_user: User, dashboard_deployer: DashboardDeployment, is_debug: bool = False):
        self._ws = ws
        self._current_user = current_user
        self._dashboard_deployer = dashboard_deployer
        self._dashboard_location = f"/Workspace/Users/{self._current_user}/Lakebridge/Dashboards"
        self._is_debug = is_debug

    def create_profiler_summary_dashboard(self, extract_file: str | None, source_tech: str | None) -> None:
        # TODO: check if the dashboard exists and unpublish it if it does
        # TODO: set the serialized dashboard JSON and warehouse ID
        logger.info("Deploying profiler summary dashboard.")
        dashboard_base_dir = (
            find_project_root(__file__) / f"src/databricks/labs/lakebridge/resources/assessments/dashboards/{source_tech}"
        )
        self._dashboard_deployer.deploy(dashboard_base_dir, recon_config)

        self._ws.dashboards.create(
            name=self.DASHBOARD_NAME,
            dashboard_filters_enabled=None,
            is_favorite=False,
            parent=self._dashboard_location,
            run_as_role=None,
            tags=None,
        )
