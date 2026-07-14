"""Post-run compression of profiler DuckDB extracts for customer sharing."""

from __future__ import annotations

import logging
from pathlib import Path

import zstandard as zstd

logger = logging.getLogger(__name__)

# Level 3 matches measured ~12x ratio with good wall-clock on multi-GB extracts.
ZSTD_LEVEL = 3


def compressed_extract_path(db_path: Path) -> Path:
    """Return the sibling ``.db.zst`` path for a profiler DuckDB file."""
    return Path(f"{db_path}.zst")


def format_bytes(num_bytes: int) -> str:
    """Human-readable size for logs and docs (binary units)."""
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def compress_profiler_db(db_path: Path, *, level: int = ZSTD_LEVEL) -> Path:
    """
    Write a zstd-compressed sibling of ``db_path`` (``*.db.zst``).

    The original ``.db`` is left in place for local inspection (DuckDB, FE tools).
    Customers should share the ``.zst`` artifact when sending extracts to Databricks.
    """
    source = db_path.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Profiler extract not found: {source}")

    dest = compressed_extract_path(source)
    compressor = zstd.ZstdCompressor(level=level, threads=-1)
    with source.open("rb") as input_handle, dest.open("wb") as output_handle:
        compressor.copy_stream(input_handle, output_handle)

    return dest


def log_share_instructions(db_path: Path, zst_path: Path) -> None:
    """Emit clear customer-facing guidance: keep .db locally, share the .zst."""
    db = db_path.expanduser()
    zst = zst_path.expanduser()
    db_size = format_bytes(db.stat().st_size) if db.is_file() else "unknown"
    zst_size = format_bytes(zst.stat().st_size) if zst.is_file() else "unknown"

    logger.info("=" * 72)
    logger.info("PROFILER OUTPUT — PLEASE READ BEFORE SHARING")
    logger.info("=" * 72)
    logger.info("Local DuckDB (keep for inspection):  %s  (%s)", db, db_size)
    logger.info("Compressed package (SHARE THIS):     %s  (%s)", zst, zst_size)
    logger.info("-" * 72)
    logger.info(
        "When sending this extract to Databricks, upload or attach the .db.zst file only. "
        "Do not send the raw .db unless your Databricks contact explicitly asks for it — "
        "the uncompressed file can be very large."
    )
    logger.info(
        "The .db remains on this machine so you can inspect it with DuckDB "
        '(e.g. duckdb "%s" "SHOW TABLES;").',
        db,
    )
    logger.info(
        "Recipients (and you, if you only keep the .zst) must decompress before opening: "
        'zstd -d "%s" -o "%s"',
        zst,
        db.name,
    )
    logger.info("=" * 72)
