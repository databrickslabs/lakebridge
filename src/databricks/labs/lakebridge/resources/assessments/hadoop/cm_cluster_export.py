"""Cloudera Manager cluster export (see cloudera_manager.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import cm_cluster_export
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="cm_cluster_export",
        endpoint="cloudera_manager",
        fetch=cm_cluster_export,
        sql_dir=Path(__file__).resolve().parent,
    )
