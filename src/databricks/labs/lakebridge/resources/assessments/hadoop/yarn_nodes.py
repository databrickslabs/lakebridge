"""YARN NodeManager inventory and utilisation (/ws/v1/cluster/nodes)."""

from pathlib import Path

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.endpoints import yarn_nodes
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_step_script

if __name__ == "__main__":
    initialize_logging()
    run_step_script(
        step="yarn_nodes",
        endpoint="yarn",
        fetch=yarn_nodes,
        sql_dir=Path(__file__).resolve().parent,
    )
