from pathlib import Path

PRODUCT_NAME = "lakebridge"
PRODUCT_PATH_PREFIX = Path.home() / ".databricks" / "labs" / PRODUCT_NAME / "lib"

PROFILER_SOURCE_SYSTEM = sorted(
    [
        "synapse",
        "snowflake",
        "oracle",
        "mssql",
        "legacy_synapse",
        "bigquery",
        "redshift",
    ]
)

SOURCE_SYSTEM_VARIANTS = {"redshift": ("serverless", "provisioned", "provisioned_multi_az")}

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
