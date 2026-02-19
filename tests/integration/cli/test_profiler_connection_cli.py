import copy
from pathlib import Path

import pytest
import yaml

from databricks.sdk import WorkspaceClient

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.cli import test_profiler_connection as check_connection


def _create_credentials_file(
    base_config: JsonObject,
    tmp_path: Path,
    *,
    exclude_serverless: bool | None = None,
    invalid_server: bool = False,
    invalid_driver: bool = False,
    missing_workspace: bool = False,
    use_same_serverless_endpoint: bool = False,
) -> Path:
    cred_path = tmp_path / ".credentials.yml"
    credentials = copy.deepcopy(base_config)

    synapse = credentials["synapse"]
    assert isinstance(synapse, dict)
    workspace = synapse["workspace"]
    assert isinstance(workspace, dict)
    profiler = synapse["profiler"]
    assert isinstance(profiler, dict)

    if exclude_serverless is not None:
        profiler["exclude_serverless_sql_pool"] = exclude_serverless
    if invalid_server:
        workspace["dedicated_sql_endpoint"] = "invalid-server.database.windows.net"
    if invalid_driver:
        workspace["driver"] = "ODBC Driver 999 for SQL Server"
    if missing_workspace:
        credentials["synapse"] = {"jdbc": synapse["jdbc"], "profiler": profiler}
    if use_same_serverless_endpoint:
        workspace["serverless_sql_endpoint"] = workspace["dedicated_sql_endpoint"]

    with open(cred_path, "w", encoding="utf-8") as f:
        yaml.dump(credentials, f)

    return cred_path


def test_profiler_connection_synapse_success(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test successful connection to Synapse dedicated SQL pool."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, exclude_serverless=True)

    check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path))

    assert "Testing connection for source technology: synapse" in caplog.text
    assert "✓ Dedicated SQL pool connection successful" in caplog.text
    assert "Connection to the source system successful" in caplog.text


def test_profiler_connection_missing_credentials_file(
    tmp_path: Path,
    ws: WorkspaceClient,
) -> None:
    """Test error handling when credential file doesn't exist."""
    non_existent_path = tmp_path / ".credentials.yml"

    with pytest.raises(ValueError, match="Connection details not found"):
        check_connection(w=ws, source_tech="synapse", cred_file_path=str(non_existent_path))


def test_profiler_connection_invalid_source_technology(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
) -> None:
    """Test error handling for unsupported source technology."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, exclude_serverless=True)

    # mssql is not in PROFILER_SOURCE_SYSTEM
    with pytest.raises(ValueError, match="Invalid source technology"):
        check_connection(w=ws, source_tech="mssql", cred_file_path=str(cred_path))


def test_profiler_connection_invalid_config_errors(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test error handling when ODBC driver is missing."""
    cred_path = _create_credentials_file(
        sandbox_synapse_cred_config, tmp_path, exclude_serverless=True, invalid_driver=True
    )

    check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path))

    assert "Missing ODBC driver" in caplog.text
    assert "Please install pre-req" in caplog.text
