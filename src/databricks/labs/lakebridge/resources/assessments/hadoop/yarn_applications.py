"""YARN applications: every application the ResourceManager lists (/ws/v1/cluster/apps), with the app filter if set."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import yarn_applications
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="yarn_applications",
        endpoint="yarn",
        fetch=yarn_applications,
        sql_dir=Path(__file__).resolve().parent,
    )
