"""Spark History Server application attempts (/api/v1/applications, attempts unnested)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import spark_applications
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="spark_application_attempts",
        endpoint="spark_history",
        fetch=spark_applications,
        sql_dir=Path(__file__).resolve().parent,
    )
