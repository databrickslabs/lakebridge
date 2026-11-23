from datetime import date, timedelta
from pathlib import Path
from unittest.mock import create_autospec

import duckdb

from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace
from databricks.labs.lakebridge.resources.assessments.synapse.workspace_extract import extract_workspace

_POOL = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Synapse/workspaces/w/bigDataPools/p"


def _notebook(name: str, compute_id: object = None) -> dict:
    metadata = {} if compute_id is None else {"a365ComputeOptions": {"id": compute_id}}
    return {"id": f"/notebooks/{name}", "name": name, "type": "Notebook", "properties": {"metadata": metadata}}


def test_extract_workspace_writes_nested_values_as_json_and_isolates_failures(tmp_path: Path) -> None:
    db_path = str(tmp_path / "extract.db")
    yesterday = date.today() - timedelta(days=1)
    workspace = create_autospec(SynapseWorkspace, instance=True)
    for method in (
        "list_sql_pools",
        "list_bigdata_pools",
        "list_linked_services",
        "list_data_flows",
        "list_pipelines",
        "list_spark_job_definitions",
        "list_sqlscripts",
        "list_libraries",
        "list_datasets",
        "list_trigger_runs",
    ):
        getattr(workspace, method).return_value = []
    workspace.get_workspace_info.return_value = {"name": "w", "properties": {"managedVirtualNetwork": "default"}}
    # A nested path holding a boolean in one notebook and a string or number in others, past DuckDB's
    # 1000-row type-inference sample.
    workspace.list_notebooks.return_value = [
        [_notebook(f"n{i}") for i in range(2000)]
        + [_notebook("off", False), _notebook("pool", _POOL), _notebook("one", 1)]
    ]
    workspace.list_triggers.side_effect = RuntimeError("triggers unavailable")
    workspace.list_pipeline_runs.side_effect = lambda day: (
        [[{"runId": "r1", "durationInMs": 5}]] if day == yesterday else []
    )

    failed = extract_workspace(workspace, db_path)

    assert failed == ["workspace_triggers"]
    with duckdb.connect(db_path, read_only=True) as conn:
        tables = {name for (name,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        compute_ids = conn.execute(
            "SELECT name, properties->>'$.metadata.a365ComputeOptions.id' FROM workspace_notebooks "
            "WHERE name IN ('off', 'pool', 'one') ORDER BY name"
        ).fetchall()
        runs = conn.execute("SELECT runId, durationInMs, last_upd FROM workspace_pipeline_runs").fetchall()
    assert {"workspace_workspace_info", "workspace_notebooks", "workspace_pipeline_runs"} <= tables
    assert "workspace_triggers" not in tables
    assert compute_ids == [("off", "false"), ("one", "1"), ("pool", _POOL)]
    assert runs == [("r1", 5, yesterday)]
