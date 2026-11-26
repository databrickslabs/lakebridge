"""Impala daemon web UI: curated flags, one row per daemon and flag.. See endpoints.py."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import impala_daemon_config
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="impala_daemon_config",
        endpoint="impala_daemons",
        fetch=impala_daemon_config,
        sql_dir=Path(__file__).resolve().parent,
    )
