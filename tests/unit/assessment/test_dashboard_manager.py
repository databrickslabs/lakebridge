# mypy: disable-error-code="attr-defined"
from unittest.mock import MagicMock, create_autospec
from pathlib import Path
from typing import cast, Any

import pytest

from databricks.sdk import WorkspaceClient, FilesAPI
from databricks.sdk.errors import PermissionDenied, NotFound, InternalError
from databricks.sdk.service.iam import User

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.lakebridge.assessments.dashboards.dashboard_manager import DashboardManager

from tests.utils.profiler_extract_utils import build_mock_synapse_extract


@pytest.fixture
def mocked_workspace_client() -> WorkspaceClient:
    ws: Any = create_autospec(WorkspaceClient, instance=True)
    ws.current_user.me.return_value = User(user_name="test_user")
    ws.files = cast(Any, create_autospec(FilesAPI, instance=True))
    ws.files.upload = cast(MagicMock, ws.files.upload)
    ws.files.upload.return_value = None
    return ws


@pytest.fixture
def dashboard_manager(mocked_workspace_client: WorkspaceClient):
    """Create a DashboardManager that uses the mocked WorkspaceClient from conftest.
    We pass the client.current_user.me() value as the current_user to avoid mocking User directly.
    """
    workspace_client = mocked_workspace_client
    installation = MockInstallation(is_global=False)
    install_state = InstallState.from_installation(installation)
    return DashboardManager(workspace_client, installation, install_state, is_debug=True)


@pytest.fixture(scope="module")
def mock_synapse_profiler_extract():
    synapse_extract_path = build_mock_synapse_extract("mock_profiler_extract")
    return synapse_extract_path


def test_upload_duckdb_to_uc_volume_file_not_found(
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    # Use a path that does not exist on disk; do not mock os.path.exists per new requirement.
    ws = mocked_workspace_client
    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path="non_existent_file.duckdb", volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
    )
    assert result is False
    ws.files.upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_invalid_volume_path(
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    ws = mocked_workspace_client
    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path="file.duckdb", volume_path="invalid_path/myfile.duckdb"
    )
    assert result is False
    ws.files.upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_success(
    tmp_path: Path,
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    # Create a real temporary file so we don't mock filesystem calls
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client

    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path=str(local_file), volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
    )
    assert result is True
    ws.files.upload.assert_called_once()


def test_upload_duckdb_to_uc_volume_failure(
    tmp_path: Path,
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client
    ws.files.upload.side_effect = Exception("Upload failed")

    with pytest.raises(Exception, match="Upload failed"):
        dashboard_manager.upload_duckdb_to_uc_volume(
            local_file_path=str(local_file), volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
        )


@pytest.mark.parametrize(
    "error_class,error_message",
    [
        (PermissionDenied, "Insufficient privileges"),
        (NotFound, "Volume path not found"),
        (InternalError, "Internal Databricks error"),
    ],
)
def test_upload_duckdb_to_uc_volume_databricks_errors(
    tmp_path: Path,
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
    error_class,
    error_message,
):
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client
    ws.files.upload.side_effect = error_class(error_message)

    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path=str(local_file), volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
    )
    assert result is False
    ws.files.upload.assert_called_once()
