"""Impala daemon web UI: queries (completed and in flight), one row per daemon and query.. See endpoints.py."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import impala_queries
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="impala_queries",
        endpoint="impala_daemons",
        fetch=impala_queries,
        sql_dir=Path(__file__).resolve().parent,
    )
