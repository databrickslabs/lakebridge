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
def pipeline_config_with_ddl(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_with_ddl.yml"))


@pytest.fixture
def pipeline_config_combined_ddl(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_with_combined_ddl.yml"))


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


def test_run_pipeline_with_ddl(
    sandbox_sqlserver: DatabaseManager,
    pipeline_config_with_ddl: PipelineConfig,
    get_logger: Logger,
) -> None:
    """Test pipeline execution with DDL steps that create tables with proper data types."""
    pipeline = PipelineClass(config=pipeline_config_with_ddl, executor=sandbox_sqlserver)
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    # Verify tables exist and have proper data types
    db_path = str(Path(pipeline_config_with_ddl.extract_folder)) + "/" + DB_NAME
    with duckdb.connect(db_path) as conn:
        # Check inventory table schema (created from DDL)
        inventory_schema = conn.execute("DESCRIBE inventory").fetchall()
        get_logger.info(f"Inventory schema: {inventory_schema}")

        # Verify column types match DDL definition
        schema_dict = {col[0]: col[1] for col in inventory_schema}
        assert schema_dict["db_id"] == "INTEGER", "db_id should be INTEGER from DDL"
        assert "VARCHAR" in schema_dict["name"], "name should be VARCHAR"
        assert "TIMESTAMP" in schema_dict["create_date"], "create_date should be TIMESTAMP"

        # Check usage table schema (created without DDL, preserves native types)
        usage_schema = conn.execute("DESCRIBE usage").fetchall()
        get_logger.info(f"Usage schema: {usage_schema}")

        # Verify table was created successfully (native types are preserved)
        assert len(usage_schema) > 0, "Usage table should have columns"

        # Verify data was inserted
        inventory_result = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()
        usage_result = conn.execute("SELECT COUNT(*) FROM usage").fetchone()
        assert inventory_result is not None and inventory_result[0] > 0, "Inventory table should have data"
        assert usage_result is not None and usage_result[0] > 0, "Usage table should have data"


def test_run_pipeline_with_combined_ddl(
    sandbox_sqlserver: DatabaseManager,
    pipeline_config_combined_ddl: PipelineConfig,
    get_logger: Logger,
) -> None:
    """Test pipeline execution with a single DDL file containing multiple CREATE TABLE statements."""
    pipeline = PipelineClass(config=pipeline_config_combined_ddl, executor=sandbox_sqlserver)
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    # Verify all tables from combined DDL were created
    db_path = str(Path(pipeline_config_combined_ddl.extract_folder)) + "/" + DB_NAME
    with duckdb.connect(db_path) as conn:
        # Check that all three tables exist
        tables = conn.execute("SHOW TABLES").fetchall()
        table_names = [table[0] for table in tables]
        get_logger.info(f"Created tables: {table_names}")

        assert "inventory" in table_names, "inventory table should exist"
        assert "usage" in table_names, "usage table should exist"
        assert "metadata" in table_names, "metadata table should exist"

        # Verify inventory table schema
        inventory_schema = conn.execute("DESCRIBE inventory").fetchall()
        get_logger.info(f"Inventory schema: {inventory_schema}")
        schema_dict = {col[0]: col[1] for col in inventory_schema}
        assert schema_dict["db_id"] == "INTEGER", "db_id should be INTEGER from combined DDL"
        assert "VARCHAR" in schema_dict["name"], "name should be VARCHAR"

        # Verify usage table schema
        usage_schema = conn.execute("DESCRIBE usage").fetchall()
        get_logger.info(f"Usage schema: {usage_schema}")
        usage_schema_dict = {col[0]: col[1] for col in usage_schema}
        assert "VARCHAR" in usage_schema_dict["sql_handle"], "sql_handle should be VARCHAR"
        assert "BIGINT" in usage_schema_dict["execution_count"], "execution_count should be BIGINT"
        assert "BIGINT" in usage_schema_dict["total_rows"], "total_rows should be BIGINT"

        # Verify metadata table schema (created but not populated)
        metadata_schema = conn.execute("DESCRIBE metadata").fetchall()
        get_logger.info(f"Metadata schema: {metadata_schema}")
        metadata_schema_dict = {col[0]: col[1] for col in metadata_schema}
        assert "VARCHAR" in metadata_schema_dict["pipeline_name"], "pipeline_name should be VARCHAR"

        # Verify data was inserted into inventory and usage tables
        inventory_result = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()
        usage_result = conn.execute("SELECT COUNT(*) FROM usage").fetchone()
        metadata_result = conn.execute("SELECT COUNT(*) FROM metadata").fetchone()

        assert inventory_result is not None and inventory_result[0] > 0, "Inventory table should have data"
        assert usage_result is not None and usage_result[0] > 0, "Usage table should have data"
        assert metadata_result is not None and metadata_result[0] == 0, "Metadata table should be empty (no data step)"
