from pathlib import Path

from databricks.labs.lakebridge.coverage import commons

if __name__ == "__main__":
    output_dir = commons.get_env_var("OUTPUT_DIR", required=True)
    if not output_dir:
        raise ValueError("Environment variable `OUTPUT_DIR` is required")
    commons.local_report(Path(output_dir))
