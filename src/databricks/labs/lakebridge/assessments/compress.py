"""Compress profiler DuckDB extracts to a sibling ``.db.zst``."""

from __future__ import annotations

from pathlib import Path

import zstandard as zstd

# Level 3 matches measured ~12x ratio with good wall-clock on multi-GB extracts.
ZSTD_LEVEL = 3


def compress_profiler_db(db_path: Path, *, level: int = ZSTD_LEVEL) -> Path:
    """Write a zstd-compressed sibling of ``db_path`` (``*.db.zst``)."""
    source = db_path.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Profiler extract not found: {source}")

    dest = Path(f"{source}.zst")
    compressor = zstd.ZstdCompressor(level=level, threads=-1)
    with source.open("rb") as input_handle, dest.open("wb") as output_handle:
        compressor.copy_stream(input_handle, output_handle)

    return dest
