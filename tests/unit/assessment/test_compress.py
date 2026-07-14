from __future__ import annotations

import logging
from pathlib import Path

from databricks.labs.lakebridge.assessments.profiler import Profiler


def test_write_compressed_extract_failure_is_non_fatal(tmp_path: Path, caplog, monkeypatch) -> None:
    """Compression errors must not fail the profile run; .db remains usable."""
    db_path = tmp_path / "extract.db"
    db_path.write_bytes(b"data")

    def _boom(_path: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr("databricks.labs.lakebridge.assessments.profiler.compress_profiler_db", _boom)

    with caplog.at_level(logging.WARNING, logger="databricks.labs.lakebridge.assessments.profiler"):
        assert Profiler._write_compressed_extract(db_path) is None

    assert any("Could not write compressed extract" in r.message for r in caplog.records)
    assert db_path.is_file()
