"""Impala catalogd web UI: tables, one row per table.. See endpoints.py."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import impala_catalog
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="impala_catalog",
        endpoint="impala_catalogd",
        fetch=impala_catalog,
        sql_dir=Path(__file__).resolve().parent,
    )
