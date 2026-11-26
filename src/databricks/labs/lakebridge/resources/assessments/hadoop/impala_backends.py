"""Impala daemon web UI: backends, one row per daemon and backend.. See endpoints.py."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import impala_backends
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="impala_backends",
        endpoint="impala_daemons",
        fetch=impala_backends,
        sql_dir=Path(__file__).resolve().parent,
    )
