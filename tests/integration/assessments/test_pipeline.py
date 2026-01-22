from collections.abc import Callable
from pathlib import Path
from logging import Logger
from typing import TypeAlias
import duckdb
import pytest

from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    DB_NAME,
    StepExecutionStatus,
    StepExecutionResult,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler

from databricks.labs.lakebridge.assessments.profiler_config import Step, PipelineConfig
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager


_Loader: TypeAlias = Callable[[Path], PipelineConfig]


@pytest.fixture
def pipeline_configuration_loader(test_resources: Path, project_path: Path, tmp_path: Path) -> _Loader:
    def _load(resource_name: Path) -> PipelineConfig:
        config_path = test_resources / "assessments" / resource_name
        return Profiler.path_modifier(config_file=config_path, path_prefix=test_resources).copy(
            extract_folder=str(tmp_path / "pipeline_output")
        )

    return _load


@pytest.fixture
def pipeline_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config.yml"))


@pytest.fixture
def pipeline_dep_failure_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_failure_dependency.yml"))


@pytest.fixture
def sql_failure_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_sql_failure.yml"))


@pytest.fixture
def python_failure_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_python_failure.yml"))


@pytest.fixture(scope="module")
def empty_result_config() -> PipelineConfig:
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config_empty_result.yml"
    config: PipelineConfig = PipelineClass.load_config_from_yaml(config_path)
    updated_steps = [step.copy(extract_source=f"{prefix}/../../{step.extract_source}") for step in config.steps]
    return config.copy(steps=updated_steps)


def test_run_pipeline(
    sandbox_sqlserver: DatabaseManager,
    pipeline_config: PipelineConfig,
    get_logger: Logger,
) -> None:
    pipeline = PipelineClass(config=pipeline_config, executor=sandbox_sqlserver)
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    assert verify_output(get_logger, Path(pipeline_config.extract_folder))


def test_run_sql_failure_pipeline(
    sandbox_sqlserver: DatabaseManager,
    sql_failure_config: PipelineConfig,
    get_logger: Logger,
) -> None:
    pipeline = PipelineClass(config=sql_failure_config, executor=sandbox_sqlserver)
    with pytest.raises(RuntimeError) as e:
        pipeline.execute()

    # Find the failed SQL step
    assert "Pipeline execution failed due to errors in steps: invalid_sql_step" in str(e.value)


def test_run_python_failure_pipeline(
    sandbox_sqlserver: DatabaseManager,
    python_failure_config: PipelineConfig,
    get_logger: Logger,
) -> None:
    pipeline = PipelineClass(config=python_failure_config, executor=sandbox_sqlserver)
    with pytest.raises(RuntimeError) as e:
        pipeline.execute()

    # Find the failed Python step
    assert "Pipeline execution failed due to errors in steps: invalid_python_step" in str(e.value)


def test_run_python_dep_failure_pipeline(
    sandbox_sqlserver: DatabaseManager,
    pipeline_dep_failure_config: PipelineConfig,
    get_logger: Logger,
):
    pipeline = PipelineClass(config=pipeline_dep_failure_config, executor=sandbox_sqlserver)
    with pytest.raises(RuntimeError) as e:
        pipeline.execute()

    # Find the failed Python step
    assert "Pipeline execution failed due to errors in steps: package_status" in str(e.value)


def test_skipped_steps(sandbox_sqlserver: DatabaseManager, pipeline_config: PipelineConfig) -> None:
    # Modify config to have some inactive steps
    inactive_steps = [step.copy(flag="inactive") for step in pipeline_config.steps]
    pipeline_config = pipeline_config.copy(steps=inactive_steps)

    pipeline = PipelineClass(config=pipeline_config, executor=sandbox_sqlserver)
    results = pipeline.execute()

    # Verify all steps are marked as skipped
    assert len(results) > 0, "Expected at least one step"
    for result in results:
        assert result.status == StepExecutionStatus.SKIPPED, f"Step {result.step_name} was not skipped"
        assert result.error_message is None, "Skipped steps should not have error messages"


def verify_output(get_logger, path):
    conn = duckdb.connect(str(Path(path).expanduser()) + "/" + DB_NAME)

    expected_tables = ["usage", "inventory", "random_data"]
    logger = get_logger
    for table in expected_tables:
        try:
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            logger.info(f"Count for {table}: {result}")
            if result is None or result[0] == 0:
                logger.debug(f"Table {table} is empty")
                return False
        except duckdb.CatalogException:
            logger.debug(f"Table {table} does not exist")
            return False

    conn.close()
    logger.info("All expected tables exist and are not empty")
    return True


def test_pipeline_config_comments() -> None:
    pipeline_w_comments = PipelineConfig(
        name="warehouse_profiler",
        version="1.0",
        extract_folder="/the/output/path",
        comment="A pipeline for extracting warehouse usage.",
    )
    pipeline_wo_comments = PipelineConfig(
        name="another_warehouse_profiler", version="1.0", extract_folder="/the/output/path"
    )
    assert pipeline_w_comments.comment == "A pipeline for extracting warehouse usage."
    assert pipeline_wo_comments.comment is None


def test_pipeline_step_comments() -> None:
    step_w_comment = Step(
        name="step_w_comment",
        type="sql",
        extract_source="path/to/extract/source.sql",
        mode="append",
        frequency="once",
        flag="active",
        comment="This is a step comment.",
    )
    step_wo_comment = Step(
        name="step_wo_comment",
        type="python",
        extract_source="path/to/extract/source.py",
        mode="overwrite",
        frequency="daily",
        flag="inactive",
    )
    assert step_w_comment.comment == "This is a step comment."
    assert step_wo_comment.comment is None


def test_run_empty_result_pipeline(
    sandbox_sqlserver: DatabaseManager,
    empty_result_config: PipelineConfig,
    get_logger: Logger,
) -> None:
    pipeline = PipelineClass(config=empty_result_config, executor=sandbox_sqlserver)
    results = pipeline.execute()

    # Verify step completed successfully despite empty results
    assert len(results) == 1
    assert results == [
        StepExecutionResult(step_name="empty_result_step", status=StepExecutionStatus.COMPLETE, error_message=None)
    ]

    # Verify that no table was created (processing was skipped for empty resultset)
    with duckdb.connect(str(Path(empty_result_config.extract_folder)) + "/" + DB_NAME) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
        table_names = [table[0] for table in tables]

    # Table should NOT be created when resultset is empty
    assert "empty_result_step" not in table_names, "Empty resultset should skip table creation"
