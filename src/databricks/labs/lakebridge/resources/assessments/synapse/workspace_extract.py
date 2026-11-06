import json
import sys
import zoneinfo
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Any

import pandas as pd
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.assessments import PRODUCT_NAME
from databricks.labs.lakebridge.connections.credential_manager import create_credential_manager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import save_to_duckdb
from databricks.labs.lakebridge.resources.assessments.synapse.common.functions import (
    create_synapse_artifacts_client,
)
from databricks.labs.lakebridge.resources.assessments.synapse.common.profiler_classes import SynapseWorkspace

logger = get_logger(__file__)


def _items(pages: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [item for page in pages for item in page]


def _runs(list_runs: Callable[[date], Iterable[list[dict[str, Any]]]], table_name: str) -> list[dict[str, Any]]:
    """The runs of each of the last 60 days, each with the day it was listed for as `last_upd`."""
    runs_list = []
    today = date.today()
    for days in range(1, 60):
        last_upd = today + timedelta(days=-days)
        runs = _items(list_runs(last_upd))
        if not runs:
            logger.warning(f"No runs found for {table_name} on {last_upd}")
            continue
        if not all(isinstance(run, dict) for run in runs):
            logger.error(f"Unexpected data export in {table_name}: {runs}")
            raise ValueError(f"Invalid data export in {table_name}")
        for run in runs:
            run["last_upd"] = last_upd
            runs_list.append(run)
    return runs_list


def _top_level_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    """One column per top-level key of the resources, with nested values as JSON text.

    Flattening every nested path into its own column made the columns, and the types DuckDB infers for
    them, depend on the workspace: a path holding a boolean in one resource and a string in another
    failed the write. Nested fields stay queryable with DuckDB's JSON functions.
    """
    return pd.DataFrame(
        [
            {
                key: json.dumps(value, default=str) if isinstance(value, dict | list) else value
                for key, value in item.items()
            }
            for item in items
        ]
    )


def extract_workspace(workspace: SynapseWorkspace, db_path: str) -> list[str]:
    """Write each workspace table and return those that failed: one table's failure doesn't drop the others."""
    extracts: dict[str, Callable[[], list[dict[str, Any]]]] = {
        "workspace_workspace_info": lambda: [workspace.get_workspace_info()],
        "workspace_sql_pools": lambda: _items(workspace.list_sql_pools()),
        "workspace_spark_pools": lambda: _items(workspace.list_bigdata_pools()),
        "workspace_linked_services": lambda: _items(workspace.list_linked_services()),
        "workspace_dataflows": lambda: _items(workspace.list_data_flows()),
        "workspace_pipelines": lambda: _items(workspace.list_pipelines()),
        "workspace_spark_jobs": lambda: _items(workspace.list_spark_job_definitions()),
        "workspace_notebooks": lambda: _items(workspace.list_notebooks()),
        "workspace_sql_scripts": lambda: _items(workspace.list_sqlscripts()),
        "workspace_triggers": lambda: _items(workspace.list_triggers()),
        "workspace_libraries": lambda: _items(workspace.list_libraries()),
        "workspace_datasets": lambda: _items(workspace.list_datasets()),
        "workspace_pipeline_runs": lambda: _runs(workspace.list_pipeline_runs, "workspace_pipeline_runs"),
        "workspace_trigger_runs": lambda: _runs(workspace.list_trigger_runs, "workspace_trigger_runs"),
    }
    failed = []
    for table_name, fetch in extracts.items():
        logger.info(f"Extraction started for {table_name}")
        try:
            save_to_duckdb(_top_level_frame(fetch()), table_name, db_path)
        except Exception as e:  # noqa: BLE001  (the remaining tables are still extracted)
            logger.error(f"Failed to extract {table_name}: {e}")
            failed.append(table_name)
    return failed


def execute():
    db_path, creds_file = arguments_loader(desc="Workspace Extract")

    cred_manager = create_credential_manager(PRODUCT_NAME, EnvGetter(), creds_file)
    synapse_workspace_settings = cred_manager.get_credentials("synapse")
    tz_info = synapse_workspace_settings["workspace"]["tz_info"]
    workspace_tz = zoneinfo.ZoneInfo(tz_info)
    workspace_name = synapse_workspace_settings["workspace"]["name"]

    logger.info(f"workspace_name: {workspace_name}")

    artifacts_client = create_synapse_artifacts_client(synapse_workspace_settings)

    try:
        # Initialize workspace settings and client
        workspace = SynapseWorkspace(workspace_tz, artifacts_client)
        failed = extract_workspace(workspace, db_path)
    except Exception as e:
        logger.error(f"Failed to extract info for Synapse Workspace Info: {str(e)}")
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)

    if failed:
        message = f"Failed to extract: {', '.join(failed)}"
        print(json.dumps({"status": "error", "message": message}), file=sys.stderr)
        sys.exit(1)

    # This is the output format expected by the pipeline.py which orchestrates the execution of this script
    print(json.dumps({"status": "success", "message": "Data loaded successfully"}))


if __name__ == '__main__':
    initialize_logging()
    execute()
