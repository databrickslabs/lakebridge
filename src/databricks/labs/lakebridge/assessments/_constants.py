from pathlib import Path

PRODUCT_NAME = "lakebridge"
PRODUCT_PATH_PREFIX = Path.home() / ".databricks" / "labs" / PRODUCT_NAME / "lib"

REDSHIFT_VARIANTS = ("serverless", "provisioned", "provisioned_multi_az")

SOURCE_SYSTEM_TO_PIPELINE_CFG = {
    "synapse": "src/databricks/labs/lakebridge/resources/assessments/synapse/pipeline_config.yml",
    "snowflake": "src/databricks/labs/lakebridge/resources/assessments/snowflake/pipeline_config.yml",
    "oracle": "src/databricks/labs/lakebridge/resources/assessments/oracle/pipeline_config.yml",
    "mssql": "src/databricks/labs/lakebridge/resources/assessments/mssql/pipeline_config.yml",
    "legacy_synapse": "src/databricks/labs/lakebridge/resources/assessments/legacy_synapse/pipeline_config.yml",
    "bigquery": "src/databricks/labs/lakebridge/resources/assessments/bigquery/pipeline_config.yml",
    **{
        f"redshift_{variant}": (
            f"src/databricks/labs/lakebridge/resources/assessments/redshift/{variant}/pipeline_config.yml"
        )
        for variant in REDSHIFT_VARIANTS
    },
}

PROFILER_SOURCE_SYSTEM = sorted(SOURCE_SYSTEM_TO_PIPELINE_CFG.keys())


# This flag indicates whether a connector is required for the source system when pipeline is trigger
# For example in the case of synapse no connector is required and the python scripts
# manage the connection by directly reading the credentials files
# Revisit this when more source systems are added to standardize the approach
CONNECTOR_REQUIRED = {
    "synapse": False,
    "mssql": True,
    "snowflake": True,
    "legacy_synapse": True,
    "oracle": True,
    "bigquery": False,
    "redshift": True,
}


def source_system_family(source: str) -> str:
    if source.startswith("redshift_"):
        return "redshift"
    return source
