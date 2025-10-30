import io
import pytest

from unittest.mock import create_autospec, MagicMock, patch
from databricks.sdk import WorkspaceClient
from databricks.labs.lakebridge.assessments.dashboards.dashboard_manager import DashboardManager

from .utils.profiler_extract_utils import build_mock_synapse_extract

@pytest.fixture
def dashboard_manager():
    workspace_client = create_autospec(WorkspaceClient)
    return DashboardManager(ws=workspace_client, is_debug=True)


@patch("os.path.exists")
def test_upload_duckdb_to_uc_volume_file_not_found(mock_exists, dashboard_manager):
    mock_exists.return_value = False
    result = dashboard_manager.upload_duckdb_to_uc_volume("non_existent_file.duckdb",
                                                          "/Volumes/catalog/schema/volume/myfile.duckdb")
    assert result is False
    dashboard_manager._ws.files.upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_invalid_volume_path(dashboard_manager):
    result = dashboard_manager.upload_duckdb_to_uc_volume("file.duckdb",
                                                          "invalid_path/myfile.duckdb")
    assert result is False
    dashboard_manager._ws.files.upload.assert_not_called()


@patch("os.path.exists")
@patch("builtins.open", new_callable=MagicMock)
def test_upload_duckdb_to_uc_volume_success(mock_open, mock_exists, dashboard_manager):
    mock_exists.return_value = True
    mock_open.return_value.__enter__.return_value.read.return_value = b"test_data"
    dashboard_manager._ws.files.upload = MagicMock()

    result = dashboard_manager.upload_duckdb_to_uc_volume("file.duckdb",
                                                          "/Volumes/catalog/schema/volume/myfile.duckdb")
    assert result is True
    dashboard_manager._ws.files.upload.assert_called_once()
    args, kwargs = dashboard_manager._ws.files.upload.call_args
    assert args[0] == "/Volumes/catalog/schema/volume/myfile.duckdb"
    assert isinstance(args[1], io.BytesIO)
    assert args[1].getvalue() == b"test_data"
    assert kwargs["overwrite"] is True


@patch("os.path.exists")
@patch("builtins.open", new_callable=MagicMock)
def test_upload_duckdb_to_uc_volume_failure(mock_open, mock_exists, dashboard_manager):
    mock_exists.return_value = True
    mock_open.return_value.__enter__.return_value.read.return_value = b"test_data"
    dashboard_manager._ws.files.upload = MagicMock(side_effect=Exception("Upload failed"))

    result = dashboard_manager.upload_duckdb_to_uc_volume("file.duckdb",
                                                          "/Volumes/catalog/schema/volume/myfile.duckdb")
    assert result is False
    dashboard_manager._ws.files.upload.assert_called_once()


@pytest.fixture(scope="module")
def mock_synapse_profiler_extract():
    synapse_extract_path = build_mock_synapse_extract("mock_profiler_extract")
    return synapse_extract_path
