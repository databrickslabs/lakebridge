"""Ranger policy items, one row per policy item (see ranger.py and endpoints.py)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import ranger_policies
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="ranger_policy_items",
        endpoint="ranger",
        fetch=ranger_policies,
        sql_dir=Path(__file__).resolve().parent,
    )
