from pathlib import Path
from typing import cast

import pytest

from databricks.labs.lakebridge.assessments.errors import ErrorCategory, SourceQueryError
from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    PipelineExecutionResult,
    StepExecutionStatus,
    status_for_source_error,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager, FetchResult


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


def _run(config: PipelineConfig, executor: _FakeExecutor, tmp_path: Path) -> PipelineExecutionResult:
    return PipelineClass(config, cast(DatabaseManager, executor), tmp_path / "out.db", tmp_path / "creds.yml").execute()


# --- Category -> status policy (the core mapping, including the "optional never rescues our bugs" guarantee) ---


@pytest.mark.parametrize(
    ("category", "optional", "expected"),
    [
        (ErrorCategory.ABSENCE, True, StepExecutionStatus.ABSENT),
        (ErrorCategory.ABSENCE, False, StepExecutionStatus.ERROR),
        (ErrorCategory.PERMISSION, True, StepExecutionStatus.ABSENT),
        (ErrorCategory.PERMISSION, False, StepExecutionStatus.ERROR),
        # Syntax/unknown are our own bugs: optional must NOT downgrade them to ABSENT.
        (ErrorCategory.SYNTAX, True, StepExecutionStatus.ERROR),
        (ErrorCategory.SYNTAX, False, StepExecutionStatus.ERROR),
        (ErrorCategory.UNKNOWN, True, StepExecutionStatus.ERROR),
        (ErrorCategory.UNKNOWN, False, StepExecutionStatus.ERROR),
    ],
)
def test_status_for_source_error(category: ErrorCategory, optional: bool, expected: StepExecutionStatus) -> None:
    assert status_for_source_error(category, optional) == expected


# --- Fatal categories abort the whole run immediately, regardless of step type or optionality ---


@pytest.mark.parametrize(
    ("category", "sqlstate"),
    [
        (ErrorCategory.CONNECTION, "08001"),
        (ErrorCategory.AUTH, "28000"),
    ],
)
def test_fatal_error_aborts_pipeline(category: ErrorCategory, sqlstate: str, tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "first.sql", "SELECT 1")
    config = _config(Step(name="first", type="sql", extract_source=query_path, optional=True))
    executor = _FakeExecutor({"SELECT 1": SourceQueryError(category, sqlstate, "cannot reach source")})

    with pytest.raises(RuntimeError, match="aborted at step 'first'"):
        _run(config, executor, tmp_path)


# --- End-to-end behaviors through execute() ---


def test_optional_absence_step_completes_pipeline(tmp_path: Path) -> None:
    required_path = _write_query(tmp_path, "required.sql", "SELECT 2")
    optional_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _config(
        Step(name="required_metric", type="sql", extract_source=required_path),
        Step(name="optional_metric", type="sql", extract_source=optional_path, optional=True),
    )
    executor = _FakeExecutor(
        {
            "SELECT 2": FetchResult({"value"}, [(1,)]),
            "SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist"),
        }
    )

    result = _run(config, executor, tmp_path)

    assert result.summary.complete == 1
    assert result.summary.absent == 1
    assert result.steps[1].status == StepExecutionStatus.ABSENT


def test_required_absence_step_fails_pipeline(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _config(Step(name="required_metric", type="sql", extract_source=query_path))
    executor = _FakeExecutor({"SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist")})

    with pytest.raises(RuntimeError, match="errors in steps: required_metric"):
        _run(config, executor, tmp_path)


def test_all_sql_steps_absent_triggers_success_floor(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _config(Step(name="optional_metric", type="sql", extract_source=query_path, optional=True))
    executor = _FakeExecutor({"SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist")})

    with pytest.raises(RuntimeError, match="every active SQL step was absent"):
        _run(config, executor, tmp_path)


def test_source_ddl_optional_absence_is_tolerated(tmp_path: Path) -> None:
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
            view_sql: SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation missing_base_table does not exist"),
            "SELECT 3": FetchResult({"value"}, [(3,)]),
        }
    )

    result = _run(config, executor, tmp_path)

    assert result.steps[0].status == StepExecutionStatus.ABSENT
    assert result.steps[1].status == StepExecutionStatus.COMPLETE


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
            required_sql: FetchResult({"sql_handle"}, [("abc",)]),
            missing_sql: SourceQueryError(ErrorCategory.ABSENCE, "42S02", "Invalid object name 'non_existent_table'."),
        }
    )

    result = _run(config, executor, tmp_path)

    assert result.summary.complete == 1
    assert result.summary.absent == 1
    assert result.steps[1].status == StepExecutionStatus.ABSENT
