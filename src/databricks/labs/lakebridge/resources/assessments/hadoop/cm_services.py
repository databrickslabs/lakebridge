"""Cloudera Manager services (see cloudera_manager.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import cm_services
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="cm_services",
        endpoint="cloudera_manager",
        fetch=cm_services,
        sql_dir=Path(__file__).resolve().parent,
    )
