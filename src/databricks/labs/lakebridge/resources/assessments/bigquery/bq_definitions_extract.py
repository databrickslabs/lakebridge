"""BigQuery object-definition (table / column / routine DDL) profiler extract entrypoint."""

from databricks.labs.lakebridge.resources.assessments.bigquery.bq_metadata_extract import (
    DEFINITION_SQL_FILE_TO_ANALYSIS_TYPE,
    run_entrypoint,
)


def execute():
    run_entrypoint(
        DEFINITION_SQL_FILE_TO_ANALYSIS_TYPE,
        "BigQuery object-definition extract complete",
        "BigQuery Object Definitions Extract Script",
    )


if __name__ == "__main__":
    execute()
