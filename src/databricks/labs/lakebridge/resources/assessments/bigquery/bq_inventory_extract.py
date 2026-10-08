"""BigQuery inventory (sizing / workload) profiler extract entrypoint."""

from databricks.labs.lakebridge.resources.assessments.bigquery.bq_metadata_extract import (
    INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
    run_entrypoint,
)


def execute():
    run_entrypoint(
        INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
        "BigQuery inventory extract complete",
        "BigQuery Inventory Extract Script",
    )


if __name__ == "__main__":
    execute()
