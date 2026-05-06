# mypy: disable-error-code="attr-defined"
from pathlib import Path
from typing import cast, Any
from types import SimpleNamespace

import pytest

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import PermissionDenied, NotFound, InternalError
from databricks.sdk.errors.platform import AlreadyExists, DatabricksError

from databricks.labs.blueprint.wheels import find_project_root
from databricks.labs.lakebridge.config import (
    ProfilerDashboardConfig,
    ProfilerDashboardMetadataConfig,
)
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardManager, ProfilerDashboardTemplateLoader


def test_upload_duckdb_to_uc_volume_file_not_found(
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
    profiler_dashboard_config,
):
    # Use a path that does not exist on disk; do not mock os.path.exists per new requirement.
    ws = mocked_workspace_client
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="non_existent_file.duckdb",
        metadata_config=ProfilerDashboardMetadataConfig(catalog="lakebridge", schema="profiler", volume="volume"),
    )
    result = dashboard_manager.upload_duckdb_to_uc_volume(config)
    assert result is False
    ws.files.upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_invalid_volume_path(
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    ws = mocked_workspace_client
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path="file.duckdb",
        metadata_config=ProfilerDashboardMetadataConfig(catalog="lakebridge", schema="profiler", volume="invalid_path"),
    )
    result = dashboard_manager.upload_duckdb_to_uc_volume(config)
    assert result is False
    ws.files.upload.assert_not_called()


def test_upload_duckdb_to_uc_volume_success(
    tmp_path: Path,
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    # Create a real temporary file so we don't mock filesystem calls
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path=str(local_file),
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge", schema="profiler", volume="ingestion_volume"
        ),
    )
    result = dashboard_manager.upload_duckdb_to_uc_volume(config)
    assert result is True
    ws.files.upload.assert_called_once()


def test_upload_duckdb_to_uc_volume_failure(
    tmp_path: Path,
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
):
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client
    ws.files.upload.side_effect = Exception("Upload failed")
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path=str(local_file),
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge", schema="profiler", volume="ingestion_volume"
        ),
    )
    with pytest.raises(Exception, match="Upload failed"):
        dashboard_manager.upload_duckdb_to_uc_volume(config)


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
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
    error_class,
    error_message,
):
    local_file = tmp_path / "file.duckdb"
    local_file.write_bytes(b"test_data")

    ws = mocked_workspace_client
    ws.files.upload.side_effect = error_class(error_message)
    config = ProfilerDashboardConfig(
        source_tech="synapse",
        extract_file_path=str(local_file),
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge", schema="profiler", volume="ingestion_volume"
        ),
    )
    result = dashboard_manager.upload_duckdb_to_uc_volume(config)
    assert result is False
    ws.files.upload.assert_called_once()


def test_create_profiler_summary_dashboard_uses_teradata_template_loader(
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
    monkeypatch: pytest.MonkeyPatch,
):
    ws = mocked_workspace_client
    cast(Any, ws).config = SimpleNamespace(warehouse_id="test-wh")
    ws.workspace.mkdirs.return_value = None
    ws.lakeview.create.return_value = SimpleNamespace(dashboard_id="dash-123")

    captured: dict[str, str] = {}

    def _fake_load(_self, source_system: str) -> dict:
        captured["source_system"] = source_system
        return {"datasets": [], "pages": []}

    monkeypatch.setattr(ProfilerDashboardTemplateLoader, "load", _fake_load)
    dashboard_cfg = ProfilerDashboardConfig(
        source_tech="teradata",
        extract_file_path="/tmp/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog="lakebridge_profiler",
            schema="profiler_runs",
            volume="ingestion_volume",
        ),
    )
    dashboard_manager.deploy(dashboard_cfg)

    assert captured.get("source_system") == "teradata"
    created_dashboard = ws.lakeview.create.call_args.kwargs["dashboard"]
    assert created_dashboard.display_name == "Lakebridge Teradata Profiler Dashboard"


def test_teradata_dashboard_template_loads_and_has_datasets():
    """The actual Teradata dashboard template should load and contain expected datasets."""
    template_folder = (
        find_project_root(__file__) / "src/databricks/labs/lakebridge/resources/assessments/dashboards/teradata"
    )
    loader = ProfilerDashboardTemplateLoader(template_folder)
    dashboard_json = loader.load(source_system="teradata")

    assert "datasets" in dashboard_json
    assert "pages" in dashboard_json
    ds_names = {ds["name"] for ds in dashboard_json["datasets"]}
    assert "ds_kpi" in ds_names
    assert "ds_sys_info" in ds_names
    assert "ds_nodes" in ds_names
    assert "ds_udfs" in ds_names
    assert len(dashboard_json["pages"]) >= 4


def testreplace_catalog_schema_substitutes_placeholders():
    """replace_catalog_schema should replace both <CATALOG_NAME> and <SCHEMA_NAME>."""
    serialized = '{"query": "SELECT * FROM <CATALOG_NAME>.<SCHEMA_NAME>.my_table"}'
    result = ProfilerDashboardManager.replace_catalog_schema(serialized, "my_catalog", "my_schema")
    assert "`my_catalog`" in result
    assert "`my_schema`" in result
    assert "<CATALOG_NAME>" not in result
    assert "<SCHEMA_NAME>" not in result


def testcreate_or_replace_dashboard_reraises_databricks_error(
    tmp_path: Path,
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
    monkeypatch: pytest.MonkeyPatch,
):
    ws = mocked_workspace_client
    cast(Any, ws).config = SimpleNamespace(warehouse_id="test-wh")
    ws.lakeview.create.side_effect = DatabricksError("create failed")

    monkeypatch.setattr(ProfilerDashboardTemplateLoader, "load", lambda _self, _source_system: {"datasets": []})

    with pytest.raises(DatabricksError):
        dashboard_manager.create_or_replace_dashboard(
            folder=tmp_path,
            ws_parent_path="/Workspace/Users/test/.lakebridge/dashboards",
            dest_catalog="lakebridge_profiler",
            dest_schema="profiler_runs",
            source_system="teradata",
        )


def test_create_or_replace_dashboard_retries_on_already_exists(
    tmp_path: Path,
    dashboard_manager: ProfilerDashboardManager,
    mocked_workspace_client: WorkspaceClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Lakeview raises AlreadyExists (not ResourceAlreadyExists) when a same-named .lvdash.json exists."""
    ws = mocked_workspace_client
    cast(Any, ws).config = SimpleNamespace(warehouse_id="test-wh")
    folder = tmp_path / "teradata"
    folder.mkdir()
    monkeypatch.setattr(ProfilerDashboardTemplateLoader, "load", lambda _self, _source_system: {"datasets": []})

    dashboard_manager._install_state.dashboards["teradata"] = "old-dashboard-id"
    ws.lakeview.create.side_effect = [
        AlreadyExists("duplicate name"),
        SimpleNamespace(dashboard_id="new-dashboard-id"),
    ]

    result = dashboard_manager.create_or_replace_dashboard(
        folder=folder,
        ws_parent_path="/Workspace/Users/test/.lakebridge/dashboards",
        dest_catalog="lakebridge_profiler",
        dest_schema="profiler_runs",
        source_system="teradata",
    )

    assert result.dashboard_id == "new-dashboard-id"
    ws.lakeview.trash.assert_called_once_with("old-dashboard-id")
    ws.workspace.delete.assert_called_once()
    assert ws.lakeview.create.call_count == 2
    assert dashboard_manager._install_state.dashboards["teradata"] == "new-dashboard-id"
