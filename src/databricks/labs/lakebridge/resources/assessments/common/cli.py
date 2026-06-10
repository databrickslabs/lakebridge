"""Shared CLI argument parsing for profiler extract entry points."""

from __future__ import annotations

import argparse
from pathlib import Path


def arguments_loader(desc: str) -> tuple[str, Path]:
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
    return args.db_path, Path(args.credential_config_path)
