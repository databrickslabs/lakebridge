"""Ambari stack versions (see ambari.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import ambari_stack_versions
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="ambari_stack_versions",
        endpoint="ambari",
        fetch=ambari_stack_versions,
        sql_dir=Path(__file__).resolve().parent,
    )
