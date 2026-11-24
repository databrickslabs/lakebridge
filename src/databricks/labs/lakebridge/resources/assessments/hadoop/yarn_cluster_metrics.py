"""YARN cluster-wide counters (/ws/v1/cluster/metrics)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import yarn_cluster_metrics
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="yarn_cluster_metrics",
        endpoint="yarn",
        fetch=yarn_cluster_metrics,
        sql_dir=Path(__file__).resolve().parent,
    )
