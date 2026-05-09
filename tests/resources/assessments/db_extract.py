import argparse
import datetime as dt
import json
import logging
import random
import sys

import duckdb
import pandas as pd


def generate_random_dataset(size: int = 10, rng=random.Random()) -> pd.DataFrame:
    # Generate dates for the last 30 days
    end_date = dt.datetime.now()
    start_date = end_date - dt.timedelta(days=30)
    dates = pd.date_range(start=start_date, end=end_date, periods=size)

    data = {
        'id': range(1, size + 1),
        'date': dates,
        'category': [rng.choice(['Low', 'Medium', 'High']) for _ in range(size)],
        'department': [rng.choice(['Sales', 'Marketing', 'Engineering', 'Support']) for _ in range(size)],
        'is_active': [rng.random() < 0.8 for _ in range(size)],
        'score': [round(rng.uniform(0, 100), 2) for _ in range(size)],
    }

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
        df = generate_random_dataset()
        logger.info(f'DataFrame columns: {df.columns}')
        # Connect to DuckDB
        conn = duckdb.connect(args.db_path)

        # Create table with appropriate schema
        conn.execute(
            """
            CREATE OR REPLACE TABLE random_data (
                id INTEGER,
                date TIMESTAMP,
                category VARCHAR,
                department VARCHAR,
                is_active BOOLEAN,
                score DOUBLE
            )
        """
        )

        conn.execute("INSERT INTO random_data SELECT * FROM df")
        conn.close()
        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    execute()
