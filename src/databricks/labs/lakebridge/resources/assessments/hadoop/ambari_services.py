"""Ambari services (see ambari.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import ambari_services
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="ambari_services",
        endpoint="ambari",
        fetch=ambari_services,
        sql_dir=Path(__file__).resolve().parent,
    )
