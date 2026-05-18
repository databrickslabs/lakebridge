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
    exclude_dedicated: bool | None = None,
    invalid_server: bool = False,
    invalid_driver: bool = False,
    missing_source_key: bool = False,
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
    if exclude_dedicated is not None:
        profiler["exclude_dedicated_sql_pools"] = exclude_dedicated
    if invalid_server:
        workspace["dedicated_sql_endpoint"] = "invalid-server.database.windows.net"
    if invalid_driver:
        workspace["driver"] = "ODBC Driver 999 for SQL Server"
    if missing_source_key:
        del credentials["synapse"]
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
    """Successful Synapse preflight should report PASS rows and exit cleanly."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, exclude_serverless=True)

    check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path))

    assert "Testing connection for source technology: synapse" in caplog.text
    # Preflight prints a report table with at least credentials + sql_auth as PASS.
    assert "credentials_integrity" in caplog.text
    assert "sql_auth" in caplog.text
    assert "PASS" in caplog.text
    assert "Connection to the source system successful" in caplog.text


def test_profiler_connection_synapse_thorough_flag(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """--thorough should still pass and suppress the thorough-mode hint."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, exclude_serverless=True)

    check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path), thorough=True)

    assert "Connection to the source system successful" in caplog.text
    # The fast-mode hint should NOT appear when --thorough was passed.
    assert "test-profiler-connection --thorough" not in caplog.text


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

    with pytest.raises(ValueError, match="Invalid source technology"):
        check_connection(w=ws, source_tech="bogus", cred_file_path=str(cred_path))


@pytest.mark.parametrize(
    ("cred_kwargs", "expected_msg"),
    [
        # invalid_driver now triggers OdbcDriverCheck FAIL, whose ConnectionError detail
        # contains "odbc_driver" which the CLI maps to the same friendly message.
        ({"exclude_serverless": True, "invalid_driver": True}, "Missing ODBC driver"),
        # invalid_server fails NetworkTlsCheck; everything else routes to the generic
        # "Connection validation failed" path.
        ({"exclude_serverless": True, "invalid_server": True}, "Connection validation failed"),
        # Both pools excluded -> ProfilerScopeCheck FAIL -> "Connection validation failed".
        ({"exclude_serverless": True, "exclude_dedicated": True}, "Connection validation failed"),
        ({"missing_source_key": True}, "Invalid credentials"),
    ],
    ids=["odbc-driver-missing", "invalid-server", "all-pools-excluded", "missing-source-key"],
)
def test_profiler_connection_error_cases(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
    cred_kwargs: dict,
    expected_msg: str,
) -> None:
    """Each failure mode should exit non-zero with the expected user-facing message."""
    cred_path = _create_credentials_file(sandbox_synapse_cred_config, tmp_path, **cred_kwargs)

    with pytest.raises(SystemExit) as exc_info:
        check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path))

    assert expected_msg in str(exc_info.value)


def test_profiler_connection_synapse_fail_fast(
    sandbox_synapse_cred_config: JsonObject,
    tmp_path: Path,
    ws: WorkspaceClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """--fail-fast on a broken config should still surface the first FATAL failure."""
    cred_path = _create_credentials_file(
        sandbox_synapse_cred_config,
        tmp_path,
        exclude_serverless=True,
        invalid_driver=True,
    )

    with pytest.raises(SystemExit) as exc_info:
        check_connection(w=ws, source_tech="synapse", cred_file_path=str(cred_path), fail_fast=True)

    assert "Missing ODBC driver" in str(exc_info.value)
