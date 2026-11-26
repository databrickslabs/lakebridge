"""Capacity scheduler queues by node label partition: configured, effective and used capacity (/ws/v1/cluster/scheduler)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import yarn_scheduler
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="yarn_scheduler_queue_partitions",
        endpoint="yarn",
        fetch=yarn_scheduler,
        sql_dir=Path(__file__).resolve().parent,
    )
