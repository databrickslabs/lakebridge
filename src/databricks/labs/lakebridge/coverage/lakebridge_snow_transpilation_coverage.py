from pathlib import Path

from databricks.labs.blueprint.wheels import ProductInfo
from databricks.labs.lakebridge.coverage import commons
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks
from databricks.labs.lakebridge.transpiler.sqlglot.parsers.snowflake import Snowflake

if __name__ == "__main__":
    input_dir = commons.get_env_var("INPUT_DIR_PARENT", required=True)
    output_dir = commons.get_env_var("OUTPUT_DIR", required=True)

    REMORPH_COMMIT_HASH = commons.get_current_commit_hash() or ""  # C0103 pylint
    product_info = ProductInfo(__file__)
    remorph_version = product_info.unreleased_version()

    if not input_dir:
        raise ValueError("Environment variable `INPUT_DIR_PARENT` is required")
    if not output_dir:
        raise ValueError("Environment variable `OUTPUT_DIR` is required")

    commons.collect_transpilation_stats(
        "Remorph",
        REMORPH_COMMIT_HASH,
        remorph_version,
        Snowflake,
        Databricks,
        Path(input_dir) / 'snowflake',
        Path(output_dir),
    )
