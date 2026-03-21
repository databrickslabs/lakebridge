# mypy: disable-error-code="attr-defined"
from unittest.mock import MagicMock, create_autospec
from pathlib import Path
from typing import cast, Any
from types import SimpleNamespace

import pytest

from databricks.sdk import WorkspaceClient, FilesAPI
from databricks.sdk.errors import PermissionDenied, NotFound, InternalError
from databricks.sdk.service.iam import User

from databricks.labs.blueprint.installation import MockInstallation
from databricks.labs.blueprint.installer import InstallState
from databricks.labs.lakebridge.assessments.dashboards.dashboard_manager import DashboardManager, DashboardTemplateLoader


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


def test_create_profiler_summary_dashboard_uses_teradata_template_loader(
    dashboard_manager: DashboardManager,
    mocked_workspace_client: WorkspaceClient,
    monkeypatch: pytest.MonkeyPatch,
):
    ws = mocked_workspace_client
    ws.config = SimpleNamespace(warehouse_id="test-wh")
    ws.workspace.mkdirs.return_value = None
    ws.lakeview.create.return_value = SimpleNamespace(dashboard_id="dash-123")

    captured: dict[str, str] = {}

    def _fake_load(self, source_system: str) -> dict:
        captured["source_system"] = source_system
        return {"datasets": [], "pages": []}

    monkeypatch.setattr(DashboardTemplateLoader, "load", _fake_load)

    dashboard_manager.create_profiler_summary_dashboard(
        source_tech="teradata",
        catalog_name="lakebridge_profiler",
        schema_name="profiler_runs",
    )

    assert captured.get("source_system") == "teradata"


def test_teradata_dashboard_template_loads_and_has_datasets():
    """The actual Teradata dashboard template should load and contain expected datasets."""
    from databricks.labs.blueprint.wheels import find_project_root

    template_folder = (
        find_project_root(__file__)
        / "src/databricks/labs/lakebridge/resources/assessments/dashboards/teradata"
    )
    loader = DashboardTemplateLoader(template_folder)
    dashboard_json = loader.load(source_system="teradata")

    assert "datasets" in dashboard_json
    assert "pages" in dashboard_json
    ds_names = {ds["name"] for ds in dashboard_json["datasets"]}
    assert "ds_kpi" in ds_names
    assert "ds_sys_info" in ds_names
    assert "ds_nodes" in ds_names
    assert "ds_udfs" in ds_names
    assert len(dashboard_json["pages"]) >= 4


def test_replace_catalog_schema_substitutes_placeholders():
    """_replace_catalog_schema should replace both <CATALOG_NAME> and <SCHEMA_NAME>."""
    serialized = '{"query": "SELECT * FROM <CATALOG_NAME>.<SCHEMA_NAME>.my_table"}'
    result = DashboardManager._replace_catalog_schema(serialized, "my_catalog", "my_schema")
    assert "`my_catalog`" in result
    assert "`my_schema`" in result
    assert "<CATALOG_NAME>" not in result
    assert "<SCHEMA_NAME>" not in result
