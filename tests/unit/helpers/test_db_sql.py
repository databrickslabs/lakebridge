from unittest.mock import patch, create_autospec

import pytest
from databricks.labs.lakebridge.helpers.db_sql import get_sql_backend
from databricks.sdk import WorkspaceClient
from databricks.labs.lsql.backends import StatementExecutionBackend


def test_get_sql_backend_with_warehouse_id_in_config():
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.config.warehouse_id = "test_warehouse_id"
    sql_backend = get_sql_backend(workspace_client)
    assert isinstance(sql_backend, StatementExecutionBackend)


def test_get_sql_backend_with_warehouse_id_in_arg():
    workspace_client = create_autospec(WorkspaceClient)
    sql_backend = get_sql_backend(workspace_client, warehouse_id="test_warehouse_id")
    assert isinstance(sql_backend, StatementExecutionBackend)


@patch('databricks.labs.lakebridge.helpers.db_sql.DatabricksConnectBackend')
def test_get_sql_backend_without_warehouse_id(databricks_connect_backend):
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.config.warehouse_id = None
    sql_backend = get_sql_backend(workspace_client)
    databricks_connect_backend.assert_called_once_with(workspace_client)
    assert isinstance(sql_backend, databricks_connect_backend.return_value.__class__)


@pytest.mark.usefixtures("monkeypatch")
@patch('databricks.labs.lakebridge.helpers.db_sql.RuntimeBackend')
def test_get_sql_backend_without_warehouse_id_in_notebook(
    runtime_backend,
    monkeypatch,
):
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "14.3")
    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.config.warehouse_id = None
    sql_backend = get_sql_backend(workspace_client)
    runtime_backend.assert_called_once()
    assert isinstance(sql_backend, runtime_backend.return_value.__class__)
