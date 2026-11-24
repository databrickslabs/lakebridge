"""Cloudera Manager host config (see cloudera_manager.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import cm_host_config
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="cm_host_config",
        endpoint="cloudera_manager",
        fetch=cm_host_config,
        sql_dir=Path(__file__).resolve().parent,
    )
