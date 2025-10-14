from pathlib import Path

PRODUCT_NAME = "lakebridge"
PRODUCT_PATH_PREFIX = Path.home() / ".databricks" / "labs" / PRODUCT_NAME / "lib"

PLATFORM_TO_SOURCE_TECHNOLOGY_CFG = {
    "synapse": "src/databricks/labs/lakebridge/resources/assessments/synapse/pipeline_config.yml",
}

# TODO modify this PLATFORM_TO_SOURCE_TECHNOLOGY.keys() once all platforms are supported
PROFILER_SOURCE_SYSTEM = ["mssql", "synapse"]


# This flag indicates whether a connector is required for the source system when pipeline is trigger
# For example in the case of synapse no connector is required and the python scripts
# manage the connection by directly reading the credentials files
# Revisit this when more source systems are added to standardize the approach
CONNECTOR_REQUIRED = {
    "synapse": False,
    "mssql": True,
}
