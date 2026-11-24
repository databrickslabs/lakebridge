"""Ranger groups (see ranger.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import ranger_groups
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="ranger_groups",
        endpoint="ranger",
        fetch=ranger_groups,
        sql_dir=Path(__file__).resolve().parent,
    )
