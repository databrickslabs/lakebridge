"""Ambari blueprint: host groups and their components (see ambari.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import ambari_blueprint
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="ambari_blueprint",
        endpoint="ambari",
        fetch=ambari_blueprint,
        sql_dir=Path(__file__).resolve().parent,
    )
