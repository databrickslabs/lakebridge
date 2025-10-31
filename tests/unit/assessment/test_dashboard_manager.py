import io
from unittest.mock import MagicMock
from pathlib import Path
from typing import Any, cast

import pytest

from databricks.sdk import WorkspaceClient


from databricks.labs.lakebridge.assessments.dashboards.dashboard_manager import DashboardManager

from tests.utils.profiler_extract_utils import build_mock_synapse_extract


@pytest.fixture
def dashboard_manager(mock_workspace_client: WorkspaceClient):
    """Create a DashboardManager that uses the mocked WorkspaceClient from conftest.
    We pass the client.current_user.me() value as the current_user to avoid mocking User directly.
    """
    workspace_client = mock_workspace_client
    current_user = workspace_client.current_user.me()
    return DashboardManager(ws=workspace_client, current_user=current_user, is_debug=True)


@pytest.fixture(scope="module")
def mock_synapse_profiler_extract():
    synapse_extract_path = build_mock_synapse_extract("mock_profiler_extract")
    return synapse_extract_path


def test_upload_duckdb_to_uc_volume_file_not_found(
    dashboard_manager: DashboardManager, mock_workspace_client: WorkspaceClient
):
    # Use a path that does not exist on disk; do not mock os.path.exists per new requirement.
    ws = mock_workspace_client
    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path="non_existent_file.duckdb", volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
    )
    assert result is False
    # Ensure the workspace client's files.upload was never called (only if it's a MagicMock)
    upload = cast(MagicMock, getattr(ws.files, "upload", None))
    if isinstance(upload, MagicMock):
        upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_invalid_volume_path(
    dashboard_manager: DashboardManager, mock_workspace_client: WorkspaceClient
):
    ws = mock_workspace_client
    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path="file.duckdb", volume_path="invalid_path/myfile.duckdb"
    )
    assert result is False
    upload = cast(MagicMock, getattr(ws.files, "upload", None))
    if isinstance(upload, MagicMock):
        upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_success(
    tmp_path: Path, dashboard_manager: DashboardManager, mock_workspace_client: WorkspaceClient
):
    # Create a real temporary file so we don't mock filesystem calls
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mock_workspace_client
    # Ensure upload method exists and is a MagicMock so we can inspect calls
    cast(Any, ws.files).upload = MagicMock()

    result = dashboard_manager.upload_duckdb_to_uc_volume(
        local_file_path=str(local_file), volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
    )
    assert result is True
    upload = cast(MagicMock, getattr(ws.files, "upload"))
    upload.assert_called_once()
    args, kwargs = upload.call_args
    assert args[0] == "/Volumes/catalog/schema/volume/myfile.duckdb"
    assert isinstance(args[1], io.BytesIO)
    assert args[1].getvalue() == b"test_data"
    assert kwargs.get("overwrite") is True


def test_upload_duckdb_to_uc_volume_failure(
    tmp_path: Path, dashboard_manager: DashboardManager, mock_workspace_client: WorkspaceClient
):
    # Create a real temporary file so we don't mock filesystem calls
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mock_workspace_client
    cast(Any, ws.files).upload = MagicMock(side_effect=Exception("Upload failed"))

    with pytest.raises(Exception, match="Upload failed"):
        dashboard_manager.upload_duckdb_to_uc_volume(
            local_file_path=str(local_file), volume_path="/Volumes/catalog/schema/volume/myfile.duckdb"
        )
    upload = cast(MagicMock, getattr(ws.files, "upload"))
    upload.assert_called_once()
