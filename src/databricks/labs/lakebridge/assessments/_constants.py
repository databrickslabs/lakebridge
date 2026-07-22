from pathlib import Path

PRODUCT_NAME = "lakebridge"
PRODUCT_PATH_PREFIX = Path.home() / ".databricks" / "labs" / PRODUCT_NAME / "lib"

# Marker used as a SOURCE_SYSTEM_VARIANTS value: the variant is auto-detected (probed) at load time.
AUTO = "__AUTO"

PROFILER_SOURCE_SYSTEM = sorted(
    [
        "synapse",
        "snowflake",
        "oracle",
        "mssql",
        "legacy_synapse",
        "bigquery",
        "redshift",
        "teradata",
    ]
)

SOURCE_SYSTEM_VARIANTS = {
    "mssql": (AUTO,),
}
