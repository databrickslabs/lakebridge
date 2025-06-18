from pathlib import Path
import duckdb
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, DB_NAME, StepExecutionStatus

from databricks.labs.lakebridge.assessments.profiler_config import Step, PipelineConfig
from ..connections.helpers import get_db_manager


@pytest.fixture()
def extractor(mock_credentials):
    return get_db_manager("remorph", "mssql")


@pytest.fixture(scope="module")
def pipeline_config():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config.yml"
    config = PipelineClass.load_config_from_yaml(config_path)

    for step in config.steps:
        step.extract_source = f"{prefix}/../../{step.extract_source}"
    return config


@pytest.fixture(scope="module")
def pipeline_dep_failure_config():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config_failure_dependency.yml"
    config = PipelineClass.load_config_from_yaml(config_path)

    for step in config.steps:
        step.extract_source = f"{prefix}/../../{step.extract_source}"
    return config


@pytest.fixture(scope="module")
def sql_failure_config():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config_sql_failure.yml"
    config = PipelineClass.load_config_from_yaml(config_path)
    for step in config.steps:
        step.extract_source = f"{prefix}/../../{step.extract_source}"
    return config


@pytest.fixture(scope="module")
def python_failure_config():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config_python_failure.yml"
    config = PipelineClass.load_config_from_yaml(config_path)
    for step in config.steps:
        step.extract_source = f"{prefix}/../../{step.extract_source}"
    return config


def test_run_pipeline(extractor, pipeline_config, get_logger):
    pipeline = PipelineClass(config=pipeline_config, executor=extractor)
    results = pipeline.execute()
    print("*******************\n")
    print(results)
    print("\n*******************")

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    assert verify_output(get_logger, pipeline_config.extract_folder)


def test_run_sql_failure_pipeline(extractor, sql_failure_config, get_logger):
    pipeline = PipelineClass(config=sql_failure_config, executor=extractor)
    results = pipeline.execute()

    # Find the failed SQL step
    failed_steps = [r for r in results if r.status == StepExecutionStatus.ERROR]
    assert len(failed_steps) > 0, "Expected at least one failed step"
    assert "SQL execution failed" in failed_steps[0].error_message


def test_run_python_failure_pipeline(extractor, python_failure_config, get_logger):
    pipeline = PipelineClass(config=python_failure_config, executor=extractor)
    results = pipeline.execute()

    # Find the failed Python step
    failed_steps = [r for r in results if r.status == StepExecutionStatus.ERROR]
    assert len(failed_steps) > 0, "Expected at least one failed step"
    assert "Script execution failed" in failed_steps[0].error_message


def test_run_python_dep_failure_pipeline(extractor, pipeline_dep_failure_config, get_logger):
    pipeline = PipelineClass(config=pipeline_dep_failure_config, executor=extractor)
    results = pipeline.execute()

    # Find the failed Python step
    failed_steps = [r for r in results if r.status == StepExecutionStatus.ERROR]
    assert len(failed_steps) > 0, "Expected at least one failed step"
    assert "Script execution failed" in failed_steps[0].error_message


def test_skipped_steps(extractor, pipeline_config, get_logger):
    # Modify config to have some inactive steps
    for step in pipeline_config.steps:
        step.flag = "inactive"

    pipeline = PipelineClass(config=pipeline_config, executor=extractor)
    results = pipeline.execute()

    # Verify all steps are marked as skipped
    assert len(results) > 0, "Expected at least one step"
    for result in results:
        assert result.status == StepExecutionStatus.SKIPPED, f"Step {result.step_name} was not skipped"
        assert result.error_message is None, "Skipped steps should not have error messages"


def verify_output(get_logger, path):
    conn = duckdb.connect(str(Path(path)) + "/" + DB_NAME)

    expected_tables = ["usage", "inventory", "random_data"]
    logger = get_logger
    for table in expected_tables:
        try:
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            logger.info(f"Count for {table}: {result[0]}")
            if result[0] == 0:
                logger.debug(f"Table {table} is empty")
                return False
        except duckdb.CatalogException:
            logger.debug(f"Table {table} does not exist")
            return False

    conn.close()
    logger.info("All expected tables exist and are not empty")
    return True


def test_pipeline_config_comments():
    pipeline_w_comments = PipelineConfig(
        name="warehouse_profiler",
        version="1.0",
        extract_folder="/tmp/extracts",
        comment="A pipeline for extracting warehouse usage.",
    )
    pipeline_wo_comments = PipelineConfig(
        name="another_warehouse_profiler", version="1.0", extract_folder="/tmp/extracts"
    )
    assert pipeline_w_comments.comment == "A pipeline for extracting warehouse usage."
    assert pipeline_wo_comments.comment is None


def test_pipeline_step_comments():
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
