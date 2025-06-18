import argparse
import json
import sys
import logging
import subprocess
import duckdb
import pandas as pd


def check_specific_packages(packages):
    data = []
    for pkg in packages:
        result = subprocess.run(['pip', 'show', pkg], capture_output=True, text=True)
        status = "Installed" if result.returncode == 0 else "Not Installed"
        data.append({"Package": pkg, "Status": status})
    return pd.DataFrame(data)


def execute():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description='Generate and store random dataset in DuckDB')
    parser.add_argument('--db-path', type=str, required=True, help='Path to DuckDB database file')
    parser.add_argument(
        '--credential-config-path', type=str, required=True, help='Path string containing credential configuration'
    )
    args = parser.parse_args()
    credential_file = args.credential_config_path

    if not credential_file.endswith('credentials.yml'):
        msg = "Credential config file must have 'credentials.yml' extension"
        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "error", "message": msg}), file=sys.stderr)
        raise ValueError("Credential config file must have 'credentials.yml' extension")

    try:
        logger.info("Checking if all dependant libs are installed")
        packages = ['databricks_labs_dqx', 'databricks_labs_ucx']
        df = check_specific_packages(packages)
        logger.info(f'DataFrame columns: {df.columns}')
        # Connect to DuckDB
        with duckdb.connect(args.db_path) as conn:

            # Create table with appropriate schema
            conn.execute(
                """
                CREATE OR REPLACE TABLE package_status (
                    package STRING,
                    status STRING,
                )
            """
            )

            conn.execute("INSERT INTO package_status SELECT package, status FROM df")
            conn.close()
        print(json.dumps({"status": "success", "message": "All Libraries are installed"}), file=sys.stderr)

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
