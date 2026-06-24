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
