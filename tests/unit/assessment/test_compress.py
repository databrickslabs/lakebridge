from __future__ import annotations

from pathlib import Path

import zstandard as zstd

from databricks.labs.lakebridge.assessments.compress import (
    compress_profiler_db,
    compressed_extract_path,
    format_bytes,
    log_share_instructions,
)


def test_format_bytes() -> None:
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.0 KiB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MiB"


def test_compressed_extract_path() -> None:
    assert compressed_extract_path(Path("/tmp/profiler_extract_snowflake_0.14.0_20260714.db")) == Path(
        "/tmp/profiler_extract_snowflake_0.14.0_20260714.db.zst"
    )


def test_compress_profiler_db_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "profiler_extract_test.db"
    payload = b"lakebridge-profiler-extract-" + (b"x" * 50_000)
    db_path.write_bytes(payload)

    zst_path = compress_profiler_db(db_path)

    assert zst_path == Path(f"{db_path}.zst")
    assert zst_path.is_file()
    assert db_path.is_file()  # original retained (Option 1)
    assert zst_path.stat().st_size < db_path.stat().st_size

    round_trip = tmp_path / "round_trip.db"
    with zst_path.open("rb") as input_handle, round_trip.open("wb") as output_handle:
        zstd.ZstdDecompressor().copy_stream(input_handle, output_handle)
    assert round_trip.read_bytes() == payload


def test_compress_profiler_db_missing_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    try:
        compress_profiler_db(missing)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_log_share_instructions(tmp_path: Path, caplog) -> None:
    import logging

    db_path = tmp_path / "extract.db"
    zst_path = tmp_path / "extract.db.zst"
    db_path.write_bytes(b"db-bytes")
    zst_path.write_bytes(b"zst-bytes")

    with caplog.at_level(logging.INFO, logger="databricks.labs.lakebridge.assessments.compress"):
        log_share_instructions(db_path, zst_path)

    joined = "\n".join(r.message for r in caplog.records)
    assert "SHARE THIS" in joined
    assert ".db.zst" in joined
    assert "Do not send the raw .db" in joined
    assert str(db_path) in joined
    assert str(zst_path) in joined


def test_profiler_compress_failure_is_non_fatal(tmp_path: Path, caplog, monkeypatch) -> None:
    import logging

    from databricks.labs.lakebridge.assessments.profiler import Profiler

    db_path = tmp_path / "extract.db"
    db_path.write_bytes(b"data")

    def _boom(_path):
        raise OSError("disk full")

    monkeypatch.setattr("databricks.labs.lakebridge.assessments.profiler.compress_profiler_db", _boom)

    with caplog.at_level(logging.WARNING, logger="databricks.labs.lakebridge.assessments.profiler"):
        Profiler._compress_for_sharing(db_path)

    assert any("Could not create compressed share package" in r.message for r in caplog.records)
    assert db_path.is_file()
