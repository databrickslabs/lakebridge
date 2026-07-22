"""Standalone script to build a Redshift-shaped profiler extract DuckDB (no live Redshift)."""

import argparse
import json
import sys
from pathlib import Path

import duckdb


def create_redshift_extract(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    # Minimal tables matching redshift_schema_def / validator tests: 3 tables, 1 empty
    conn.execute("""
        CREATE OR REPLACE TABLE query_view (
            user_id BIGINT,
            query_id BIGINT,
            query_text VARCHAR,
            start_time VARCHAR,
            end_time VARCHAR,
            query_group VARCHAR,
            query_type VARCHAR
        )
    """)
    conn.execute("INSERT INTO query_view SELECT 1, 1, 'x', '2020-01-01', '2020-01-01', 'g', 'SELECT' FROM range(10)")
    conn.execute("""
        CREATE OR REPLACE TABLE rs_managed_storage_gb (
            set_name VARCHAR,
            rs_managed_storage_gb DOUBLE
        )
    """)
    conn.execute("INSERT INTO rs_managed_storage_gb SELECT 's1', 1.0 FROM range(10)")
    conn.execute("""
        CREATE OR REPLACE TABLE rs_nodes (
            set_name VARCHAR,
            rs_nodes_type VARCHAR,
            rs_number_of_nodes BIGINT,
            compute_seconds BIGINT
        )
    """)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=str, required=True)
    parser.add_argument("--credential-config-path", type=str, required=True)
    args = parser.parse_args()
    if not args.credential_config_path.endswith("credentials.yml"):
        print(
            json.dumps({"status": "error", "message": "Credential config file must have 'credentials.yml' extension"}),
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        create_redshift_extract(args.db_path)
        print(json.dumps({"status": "success", "message": "Data loaded successfully"}))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
