from pathlib import Path
from typing import cast

import pytest

from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    StepExecutionResult,
    StepExecutionStatus,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import FetchResult, DatabaseConnector


class _FakeExecutor:
    """Maps exact (stripped) query text to a FetchResult or an exception to raise."""

    def __init__(self, responses: dict[str, Exception | FetchResult]) -> None:
        self._responses = {query.strip(): response for query, response in responses.items()}

    def fetch(self, query: str) -> FetchResult:
        key = query.strip()
        if key not in self._responses:
            raise AssertionError(f"unexpected query: {key}")
        response = self._responses[key]
        if isinstance(response, Exception):
            raise response
        return response


def _config(*steps: Step) -> PipelineConfig:
    return PipelineConfig(name="policy_test", version="1.0", steps=list(steps))


def _write_query(tmp_path: Path, name: str, sql: str) -> str:
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return str(path)


def _run(config: PipelineConfig, executor: _FakeExecutor, tmp_path: Path) -> list[StepExecutionResult]:
    return PipelineClass(
        config, cast(DatabaseConnector, executor), tmp_path / "out.db", tmp_path / "creds.yml"
    ).execute()


def test_optional_step_tolerates_any_failure(tmp_path: Path) -> None:
    required_path = _write_query(tmp_path, "required.sql", "SELECT 2")
    optional_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _config(
        Step(name="required_metric", type="sql", extract_source=required_path),
        Step(name="optional_metric", type="sql", extract_source=optional_path, optional=True),
    )
    executor = _FakeExecutor(
        {
            "SELECT 2": FetchResult(["value"], [(1,)]),
            "SELECT 1": ConnectionError("Database query failed: relation does not exist"),
        }
    )

    results = _run(config, executor, tmp_path)

    assert results[0].status == StepExecutionStatus.COMPLETE
    assert results[1].status == StepExecutionStatus.ABSENT
    assert results[1].error_message == "Database query failed: relation does not exist"


def test_optional_step_tolerates_unclassified_driver_errors(tmp_path: Path) -> None:
    """Optional must tolerate failures even when the driver message is opaque."""
    required_path = _write_query(tmp_path, "required.sql", "SELECT 2")
    optional_path = _write_query(tmp_path, "opaque.sql", "SELECT 1")
    config = _config(
        Step(name="required_metric", type="sql", extract_source=required_path),
        Step(name="optional_metric", type="sql", extract_source=optional_path, optional=True),
    )
    executor = _FakeExecutor(
        {
            "SELECT 2": FetchResult(["value"], [(1,)]),
            "SELECT 1": ConnectionError("Database query failed: driver said something opaque"),
        }
    )

    results = _run(config, executor, tmp_path)

    assert results[1].status == StepExecutionStatus.ABSENT
    assert results[1].error_message == "Database query failed: driver said something opaque"


def test_required_step_failure_fails_pipeline(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _config(Step(name="required_metric", type="sql", extract_source=query_path))
    executor = _FakeExecutor({"SELECT 1": ConnectionError("Database query failed: relation does not exist")})

    with pytest.raises(RuntimeError, match="errors in steps: required_metric"):
        _run(config, executor, tmp_path)


def test_source_ddl_optional_failure_is_tolerated(tmp_path: Path) -> None:
    """A wrong-variant view create referencing a missing base object degrades to ABSENT, not abort."""
    view_sql = "create view query_view as select * from missing_base_table;"
    ddl_path = _write_query(tmp_path, "view.sql", view_sql)
    metric_path = _write_query(tmp_path, "metric.sql", "SELECT 3")
    config = _config(
        Step(name="query_view", type="source_ddl", extract_source=ddl_path, optional=True),
        Step(name="metric", type="sql", extract_source=metric_path),
    )
    executor = _FakeExecutor(
        {
            view_sql: ConnectionError("Database query failed: relation missing_base_table does not exist"),
            "SELECT 3": FetchResult(["value"], [(3,)]),
        }
    )

    results = _run(config, executor, tmp_path)

    assert results[0].status == StepExecutionStatus.ABSENT
    assert results[1].status == StepExecutionStatus.COMPLETE


def test_ddl_failure_is_fatal(tmp_path: Path) -> None:
    """Local DuckDB DDL targets our own schema; a failure there is our bug and must stay fatal."""
    ddl_path = _write_query(tmp_path, "bad_ddl.sql", "THIS IS NOT VALID DDL;")
    config = _config(Step(name="broken_table", type="ddl", extract_source=ddl_path))
    executor = _FakeExecutor({})

    with pytest.raises(RuntimeError, match="error in DDL step: broken_table"):
        _run(config, executor, tmp_path)


def test_optional_absence_integration_fixture(test_resources: Path, tmp_path: Path) -> None:
    """End-to-end YAML loading with optional: true, using the shared fixture config."""
    config_path = test_resources / "assessments" / "pipeline_config_optional_absence.yml"
    config = Profiler.path_modifier(config_file=config_path, path_prefix=test_resources)
    required_sql = (test_resources / "assessments" / "usage.sql").read_text(encoding="utf-8")
    missing_sql = (test_resources / "assessments" / "missing_table_query.sql").read_text(encoding="utf-8")
    executor = _FakeExecutor(
        {
            required_sql: FetchResult(["sql_handle"], [("abc",)]),
            missing_sql: ConnectionError("Database query failed: Invalid object name 'non_existent_table'."),
        }
    )

    results = _run(config, executor, tmp_path)

    assert results[0].status == StepExecutionStatus.COMPLETE
    assert results[1].status == StepExecutionStatus.ABSENT
    assert results[1].error_message
