from collections.abc import Iterator
from pathlib import Path
from typing import cast

import duckdb
import pyarrow as pa
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, FetchResult


class _Executor:
    def __init__(
        self,
        *,
        result: FetchResult | Exception | None = None,
        batches: list[pa.Table] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self._result = result
        self._batches = batches
        self._stream_error = stream_error

    def supports_streaming(self) -> bool:
        return self._batches is not None

    def fetch(self, _query: str) -> FetchResult:
        if isinstance(self._result, Exception):
            raise self._result
        assert self._result is not None
        return self._result

    def stream(self, _query: str) -> Iterator[pa.Table]:
        assert self._batches is not None
        yield from self._batches
        if self._stream_error:
            raise self._stream_error


def _pipeline(tmp_path: Path, executor: _Executor, *, ddl: str = "CREATE TABLE metric (value BIGINT)") -> PipelineClass:
    query_path = tmp_path / "metric.sql"
    query_path.write_text("SELECT value FROM source", encoding="utf-8")
    ddl_path = tmp_path / "metric_ddl.sql"
    ddl_path.write_text(ddl, encoding="utf-8")
    step = Step(
        name="metric",
        type="sql",
        extract_source=str(query_path),
        ddl_source=str(ddl_path),
        mode="overwrite",
    )
    config = PipelineConfig(name="test", version="1.0", steps=[step])
    return PipelineClass(
        config,
        cast(DatabaseConnector, executor),
        tmp_path / "profiler.db",
        tmp_path / "credentials.yml",
    )


def _seed_previous_result(db_path: Path) -> None:
    with duckdb.connect(db_path) as conn:
        conn.execute("CREATE TABLE metric (value BIGINT)")
        conn.execute("INSERT INTO metric VALUES (99)")


def _metric_values(db_path: Path) -> list[int]:
    with duckdb.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT value FROM metric ORDER BY value").fetchall()]


def test_failed_fetch_preserves_previous_overwrite_result(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _Executor(result=ConnectionError("source failed")))
    _seed_previous_result(tmp_path / "profiler.db")

    with pytest.raises(RuntimeError, match="metric"):
        pipeline.execute()

    assert _metric_values(tmp_path / "profiler.db") == [99]


def test_failed_stream_rolls_back_previous_overwrite_result(tmp_path: Path) -> None:
    batch = pa.table({"value": [1]})
    pipeline = _pipeline(
        tmp_path,
        _Executor(batches=[batch], stream_error=ConnectionError("source failed")),
    )
    _seed_previous_result(tmp_path / "profiler.db")

    with pytest.raises(RuntimeError, match="metric"):
        pipeline.execute()

    assert _metric_values(tmp_path / "profiler.db") == [99]


def test_stream_uses_explicit_ddl_and_commits_after_completion(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _Executor(batches=[pa.table({"value": [2, 1]})]))

    pipeline.execute()

    assert _metric_values(tmp_path / "profiler.db") == [1, 2]


def test_optional_step_does_not_tolerate_invalid_duckdb_ddl(tmp_path: Path) -> None:
    pipeline = _pipeline(
        tmp_path,
        _Executor(result=FetchResult(["value"], [(1,)])),
        ddl="CREATE TABLE wrong_name (value BIGINT)",
    )
    pipeline.config = pipeline.config.copy(steps=[pipeline.config.steps[0].copy(optional=True)])

    with pytest.raises(RuntimeError, match="DDL step: metric"):
        pipeline.execute()
