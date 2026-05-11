"""Shared CLI argument parsing for profiler extract entry points."""

from __future__ import annotations

import argparse
import json
import sys


def arguments_loader(desc: str) -> tuple[str, str]:
    """Parse the standard ``--db-path`` / ``--credential-config-path`` arguments.

    All profiler extract scripts (Synapse, MSSQL, …) are launched the same way
    by ``pipeline.py``, so they share this argument parser.

    Returns:
        A ``(db_path, credential_file)`` tuple.

    Raises:
        ValueError: if ``credential_file`` does not end in ``credentials.yml``.
    """
    parser = argparse.ArgumentParser(description=desc)
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

    # file exists check takes place within the entry point so not replicating this here check cli.py execute-profiler

    return args.db_path, credential_file
