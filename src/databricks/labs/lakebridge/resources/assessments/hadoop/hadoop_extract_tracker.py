"""The run's final step: records in the extract tracker that the initial extract completed. Writes no table."""

from databricks.labs.lakebridge import initialize_logging
from databricks.labs.lakebridge.resources.assessments.hadoop.extract import run_finish_script

if __name__ == "__main__":
    initialize_logging()
    run_finish_script()
