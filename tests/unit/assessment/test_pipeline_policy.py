from pathlib import Path

import pytest
import yaml

from databricks.labs.lakebridge.assessments.errors import ErrorCategory, SourceQueryError
from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    PipelineExecutionResult,
    StepExecutionStatus,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import FetchResult


class _FakeExecutor:
    def __init__(self, responses: dict[str, Exception | FetchResult] | None = None) -> None:
        self._responses = responses or {}

    def fetch(self, query: str) -> FetchResult:
        key = query.strip()
        response = self._responses.get(key)
        if response is None and "non_existent_table" in query:
            response = SourceQueryError(
                ErrorCategory.ABSENCE,
                "42S02",
                "Invalid object name 'non_existent_table'.",
            )
        if response is None and "DM_EXEC_QUERY_STATS" in query:
            response = FetchResult({"sql_handle"}, [("abc",)])
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise SourceQueryError(ErrorCategory.UNKNOWN, None, f"unexpected query: {key}")
        return response


def _pipeline_config(*steps: Step) -> PipelineConfig:
    return PipelineConfig(name="policy_test", version="1.0", steps=list(steps))


def _write_query(tmp_path: Path, name: str, sql: str) -> str:
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return str(path)


def test_optional_absence_step_completes_pipeline(tmp_path: Path) -> None:
    required_path = _write_query(tmp_path, "required.sql", "SELECT 2")
    optional_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _pipeline_config(
        Step(name="required_metric", type="sql", extract_source=required_path),
        Step(
            name="optional_metric",
            type="sql",
            extract_source=optional_path,
            optional=True,
        ),
    )
    executor = _FakeExecutor(
        {
            "SELECT 2": FetchResult({"value"}, [(1,)]),
            "SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist"),
        }
    )

    result = PipelineClass(config, executor, tmp_path / "out.db", tmp_path / "creds.yml").execute()

    assert result.summary.complete == 1
    assert result.summary.absent == 1
    assert result.steps[1].status == StepExecutionStatus.ABSENT


def test_required_absence_step_fails_pipeline(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _pipeline_config(
        Step(
            name="required_metric",
            type="sql",
            extract_source=query_path,
        )
    )
    executor = _FakeExecutor(
        {
            "SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist"),
        }
    )

    with pytest.raises(RuntimeError, match="errors in steps: required_metric"):
        PipelineClass(config, executor, tmp_path / "out.db", tmp_path / "creds.yml").execute()


def test_fatal_connection_error_aborts_pipeline(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "query.sql", "SELECT 1")
    config = _pipeline_config(
        Step(name="first", type="sql", extract_source=query_path),
    )
    executor = _FakeExecutor(
        {
            "SELECT 1": SourceQueryError(ErrorCategory.CONNECTION, "08001", "connection refused"),
        }
    )

    with pytest.raises(RuntimeError, match="aborted at step 'first'"):
        PipelineClass(config, executor, tmp_path / "out.db", tmp_path / "creds.yml").execute()


def test_all_sql_steps_absent_triggers_success_floor(tmp_path: Path) -> None:
    query_path = _write_query(tmp_path, "missing.sql", "SELECT 1")
    config = _pipeline_config(
        Step(
            name="optional_metric",
            type="sql",
            extract_source=query_path,
            optional=True,
        )
    )
    executor = _FakeExecutor(
        {
            "SELECT 1": SourceQueryError(ErrorCategory.ABSENCE, "42P01", "relation does not exist"),
        }
    )

    with pytest.raises(RuntimeError, match="every active SQL step was absent"):
        PipelineClass(config, executor, tmp_path / "out.db", tmp_path / "creds.yml").execute()


def test_step_optional_round_trips_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "pipeline_config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "name": "yaml_test",
                "version": "1.0",
                "steps": [
                    {
                        "name": "metric",
                        "type": "sql",
                        "extract_source": "metric.sql",
                        "optional": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = PipelineClass.load_config_from_yaml(config_path)
    assert config.steps[0].optional is True


def test_step_optional_defaults_false() -> None:
    step = Step(name="metric", type="sql", extract_source="metric.sql")
    assert step.optional is False


def test_optional_absence_integration_fixture(
    test_resources: Path,
    tmp_path: Path,
) -> None:
    config_path = test_resources / "assessments" / "pipeline_config_optional_absence.yml"
    config = Profiler.path_modifier(config_file=config_path, path_prefix=test_resources)
    executor = _FakeExecutor()

    result: PipelineExecutionResult = PipelineClass(
        config,
        executor,
        tmp_path / "out.db",
        tmp_path / "creds.yml",
    ).execute()

    assert result.summary.complete == 1
    assert result.summary.absent == 1
    assert result.steps[1].status == StepExecutionStatus.ABSENT
