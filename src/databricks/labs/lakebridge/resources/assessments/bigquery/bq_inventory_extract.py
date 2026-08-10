"""BigQuery inventory (sizing / workload) profiler extract entrypoint."""

from databricks.labs.lakebridge.resources.assessments.bigquery.bq_metadata_extract import (
    INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
    run_entrypoint,
)

if __name__ == "__main__":
    run_entrypoint(
        INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
        "BigQuery inventory extract complete",
        "BigQuery Inventory Extract Script",
    )
