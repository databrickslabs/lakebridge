import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.cli import test_profiler_connection as check_connection


def _create_credentials_file(base_config: dict[str, Any], tmp_path: Path, **modifications: Any) -> Path:
    """Helper to create credential files with modifications.

    Args:
        base_config: Base credential configuration to copy
        tmp_path: Temporary directory path
        **modifications: Keyword arguments to modify the config:
            - exclude_serverless: Set profiler.exclude_serverless_sql_pool
            - invalid_server: Use invalid dedicated_sql_endpoint
            - invalid_driver: Use non-existent ODBC driver
            - missing_workspace: Remove workspace section entirely
            - use_same_serverless_endpoint: Copy dedicated endpoint to serverless

    Returns:
        Path to the created credentials file
    """
    cred_path = tmp_path / ".credentials.yml"
    credentials = copy.deepcopy(base_config)

    for key, value in modifications.items():
        match key:
            case "exclude_serverless":
                credentials["synapse"]["profiler"]["exclude_serverless_sql_pool"] = value
            case "invalid_server" if value:
                credentials["synapse"]["workspace"]["dedicated_sql_endpoint"] = "invalid-server.database.windows.net"
            case "invalid_driver" if value:
                credentials["synapse"]["workspace"]["driver"] = "ODBC Driver 999 for SQL Server"
            case "missing_workspace" if value:
                # Keep jdbc and profiler sections, remove workspace
                credentials["synapse"] = {
                    "jdbc": credentials["synapse"]["jdbc"],
                    "profiler": credentials["synapse"]["profiler"],
                }
            case "use_same_serverless_endpoint" if value:
                credentials["synapse"]["workspace"]["serverless_sql_endpoint"] = credentials["synapse"]["workspace"][
                    "dedicated_sql_endpoint"
                ]

    with open(cred_path, "w", encoding="utf-8") as f:
        yaml.dump(credentials, f)

    return cred_path


def test_profiler_connection_synapse_success(
    sandbox_synapse_cred_config: dict[str, Any],
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog,
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
    sandbox_synapse_cred_config: dict[str, Any],
    tmp_path: Path,
    ws: WorkspaceClient,
) -> None:
    """Test error handling for unsupported source technology."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, exclude_serverless=True)

    # mssql is not in PROFILER_SOURCE_SYSTEM
    with pytest.raises(ValueError, match="Invalid source technology"):
        check_connection(w=ws, source_tech="mssql", cred_file_path=str(cred_path))


def test_profiler_connection_invalid_config_errors(
    sandbox_synapse_cred_config: dict[str, Any],
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog,
) -> None:
    """Test error handling when ODBC driver is missing."""
    cred_path = _create_credentials_file(
        sandbox_synapse_cred_config, tmp_path, exclude_serverless=True, invalid_driver=True
    )

    check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path))

    assert "Missing ODBC driver" in caplog.text
    assert "Please install pre-req" in caplog.text
