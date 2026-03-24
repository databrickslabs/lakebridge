# mypy: disable-error-code="attr-defined"
from unittest.mock import MagicMock, create_autospec
from typing import cast, Any

import pytest

from databricks.sdk import WorkspaceClient, FilesAPI
from databricks.sdk.service.iam import User

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.lakebridge.config import (
    ProfilerDashboardConfig,
    ProfilerDashboardMetadataConfig,
)
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardManager


@pytest.fixture
def mocked_workspace_client() -> WorkspaceClient:
    ws: Any = create_autospec(WorkspaceClient, instance=True)
    ws.current_user.me.return_value = User(user_name="test_user")
    ws.files = cast(Any, create_autospec(FilesAPI, instance=True))
    ws.files.upload = cast(MagicMock, ws.files.upload)
    ws.files.upload.return_value = None
    return ws


@pytest.fixture
def profiler_dashboard_config() -> ProfilerDashboardConfig:
    return ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="/tmp/data/synapse_assessment/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge", schema="profiler", volume="ingestion_volume"
        ),
    )


@pytest.fixture
def dashboard_manager(mocked_workspace_client: WorkspaceClient):
    """Create a DashboardManager that uses the mocked WorkspaceClient from conftest."""
    workspace_client = mocked_workspace_client
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    return ProfilerDashboardManager(workspace_client, installation, install_state)
