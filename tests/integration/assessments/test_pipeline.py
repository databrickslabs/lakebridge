from collections.abc import Callable
from logging import Logger
from pathlib import Path
from typing import TypeAlias

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    StepExecutionResult,
    StepExecutionStatus,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector

_Loader: TypeAlias = Callable[[Path], PipelineConfig]

_DB_FILE = "test_profiler.db"
_CREDS_FILE = "test_creds.yml"


@pytest.fixture
def pipeline_configuration_loader(test_resources: Path) -> _Loader:
    def _load(resource_name: Path) -> PipelineConfig:
        config_path = test_resources / "assessments" / resource_name
        return Profiler.path_modifier(config_file=config_path, path_prefix=test_resources)

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
def sql_failure_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_sql_failure.yml"))


@pytest.fixture
def optional_absence_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_optional_absence.yml"))


@pytest.fixture
def python_failure_config(pipeline_configuration_loader: _Loader) -> PipelineConfig:
    return pipeline_configuration_loader(Path("pipeline_config_python_failure.yml"))


@pytest.fixture(scope="module")
def empty_result_config() -> PipelineConfig:
    prefix = Path(__file__).parent
    config_path = prefix / ".." / ".." / "resources" / "assessments" / "pipeline_config_empty_result.yml"
    config: PipelineConfig = PipelineClass.load_config_from_yaml(config_path)
    test_root = prefix / ".." / ".."
    updated_steps = []
    for step in config.steps:
        changes: dict[str, str] = {"extract_source": str(test_root / step.extract_source)}
        if step.ddl_source is not None:
            changes["ddl_source"] = str(test_root / step.ddl_source)
        updated_steps.append(step.copy(**changes))
    return config.copy(steps=updated_steps)


def test_run_pipeline(
    sandbox_sqlserver: DatabaseConnector,
    pipeline_config: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    pipeline = PipelineClass(
        config=pipeline_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    assert verify_output(get_logger, tmp_path / _DB_FILE)


def test_run_sql_failure_pipeline(
    sandbox_sqlserver: DatabaseConnector,
    sql_failure_config: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    pipeline = PipelineClass(
        config=sql_failure_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    with pytest.raises(RuntimeError) as e:
        pipeline.execute()

    # Find the failed SQL step
    assert "Pipeline execution failed due to errors in steps: invalid_sql_step" in str(e.value)


def test_run_optional_absence_pipeline(
    sandbox_sqlserver: DatabaseConnector,
    optional_absence_config: PipelineConfig,
    tmp_path: Path,
) -> None:
    """A missing object on the real source, marked optional, is tolerated instead of aborting.

    Exercises the live MSSQL driver: the missing-table error becomes ConnectionError, the optional
    step degrades to ABSENT, and the required step still completes so the run succeeds.
    """
    pipeline = PipelineClass(
        config=optional_absence_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    statuses = {r.step_name: r.status for r in results}
    assert statuses["required_metric"] == StepExecutionStatus.COMPLETE
    assert statuses["optional_missing_table"] == StepExecutionStatus.ABSENT
    absent = next(r for r in results if r.step_name == "optional_missing_table")
    assert absent.error_message


def test_run_python_failure_pipeline(
    sandbox_sqlserver: DatabaseConnector,
    python_failure_config: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    pipeline = PipelineClass(
        config=python_failure_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    with pytest.raises(RuntimeError) as e:
        pipeline.execute()

    # Find the failed Python step
    assert "Pipeline execution failed due to errors in steps: invalid_python_step" in str(e.value)


def test_skipped_steps(
    sandbox_sqlserver: DatabaseConnector,
    pipeline_config: PipelineConfig,
    tmp_path: Path,
) -> None:
    # Modify config to have some inactive steps
    inactive_steps = [step.copy(flag="inactive") for step in pipeline_config.steps]
    pipeline_config = pipeline_config.copy(steps=inactive_steps)

    pipeline = PipelineClass(
        config=pipeline_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    # Verify all steps are marked as skipped
    assert len(results) > 0, "Expected at least one step"
    for result in results:
        assert result.status == StepExecutionStatus.SKIPPED, f"Step {result.step_name} was not skipped"
        assert result.error_message is None, "Skipped steps should not have error messages"


def verify_output(get_logger, path):
    expected_tables = ["usage", "inventory", "random_data"]
    expected_columns = {
        "inventory": ["db_id", "name", "collation_name", "create_date", "extract_ts"],
        "usage": [
            "sql_handle",
            "creation_time",
            "last_execution_time",
            "execution_count",
            "total_worker_time",
            "total_elapsed_time",
            "total_rows",
        ],
    }

    logger = get_logger
    conn = duckdb.connect(path)

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

    for table, expected in expected_columns.items():
        actual = [desc[0] for desc in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        if actual != expected:
            logger.debug(f"Table {table} has columns {actual}, expected {expected}")
            return False

    conn.close()
    logger.info("All expected tables and columns exist and are not empty")
    return True


def test_pipeline_config_comments() -> None:
    pipeline_w_comments = PipelineConfig(
        name="warehouse_profiler",
        version="1.0",
        comment="A pipeline for extracting warehouse usage.",
    )
    pipeline_wo_comments = PipelineConfig(name="another_warehouse_profiler", version="1.0")
    assert pipeline_w_comments.comment == "A pipeline for extracting warehouse usage."
    assert pipeline_wo_comments.comment is None


def test_pipeline_step_comments() -> None:
    step_w_comment = Step(
        name="step_w_comment",
        type="sql",
        extract_source="path/to/extract/source.sql",
        ddl_source="path/to/extract/source_ddl.sql",
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
    sandbox_sqlserver: DatabaseConnector,
    empty_result_config: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    pipeline = PipelineClass(
        config=empty_result_config,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    # Verify step completed successfully despite empty results
    assert len(results) == 1
    assert results == [
        StepExecutionResult(step_name="empty_result_step", status=StepExecutionStatus.COMPLETE, error_message=None)
    ]

    # Verify that DDL still created the empty typed table
    with duckdb.connect(tmp_path / _DB_FILE) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
        table_names = [table[0] for table in tables]

    assert "empty_result_step" in table_names, "Empty resultset should still apply DDL schema"
    with duckdb.connect(tmp_path / _DB_FILE) as conn:
        count = conn.execute("SELECT COUNT(*) FROM empty_result_step").fetchone()
        assert count is not None and count[0] == 0
        schema = {col[0]: col[1] for col in conn.execute("DESCRIBE empty_result_step").fetchall()}
        assert "col1" in schema
        assert "col2" in schema
        assert "col3" in schema


def test_run_pipeline_with_ddl(
    sandbox_sqlserver: DatabaseConnector,
    pipeline_config_with_ddl: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    """Test pipeline execution with per-step DDL that creates tables with proper data types."""
    pipeline = PipelineClass(
        config=pipeline_config_with_ddl,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    # Verify tables exist and have proper data types
    with duckdb.connect(tmp_path / _DB_FILE) as conn:
        # Check inventory table schema (created from DDL)
        inventory_schema = conn.execute("DESCRIBE inventory").fetchall()
        get_logger.info(f"Inventory schema: {inventory_schema}")

        # Verify column types match DDL definition
        schema_dict = {col[0]: col[1] for col in inventory_schema}
        assert schema_dict["db_id"] == "INTEGER", "db_id should be INTEGER from DDL"
        assert "VARCHAR" in schema_dict["name"], "name should be VARCHAR"
        assert "TIMESTAMP" in schema_dict["create_date"], "create_date should be TIMESTAMP"

        # Check usage table schema (also from DDL)
        usage_schema = conn.execute("DESCRIBE usage").fetchall()
        get_logger.info(f"Usage schema: {usage_schema}")
        usage_schema_dict = {col[0]: col[1] for col in usage_schema}
        assert "VARCHAR" in usage_schema_dict["sql_handle"], "sql_handle should be VARCHAR"
        assert "BIGINT" in usage_schema_dict["execution_count"], "execution_count should be BIGINT"

        # Verify data was inserted
        inventory_result = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()
        usage_result = conn.execute("SELECT COUNT(*) FROM usage").fetchone()
        assert inventory_result is not None and inventory_result[0] > 0, "Inventory table should have data"
        assert usage_result is not None and usage_result[0] > 0, "Usage table should have data"


def test_run_pipeline_with_combined_ddl(
    sandbox_sqlserver: DatabaseConnector,
    pipeline_config_combined_ddl: PipelineConfig,
    get_logger: Logger,
    tmp_path: Path,
) -> None:
    """Test pipeline execution where each SQL step carries its own DuckDB DDL."""
    pipeline = PipelineClass(
        config=pipeline_config_combined_ddl,
        executor=sandbox_sqlserver,
        db_path=tmp_path / _DB_FILE,
        cred_file_path=tmp_path / _CREDS_FILE,
    )
    results = pipeline.execute()

    # Verify all steps completed successfully
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed with status {result.status}"

    with duckdb.connect(tmp_path / _DB_FILE) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
        table_names = [table[0] for table in tables]
        get_logger.info(f"Created tables: {table_names}")

        assert "inventory" in table_names, "inventory table should exist"
        assert "usage" in table_names, "usage table should exist"

        inventory_schema = conn.execute("DESCRIBE inventory").fetchall()
        get_logger.info(f"Inventory schema: {inventory_schema}")
        schema_dict = {col[0]: col[1] for col in inventory_schema}
        assert schema_dict["db_id"] == "INTEGER", "db_id should be INTEGER from DDL"
        assert "VARCHAR" in schema_dict["name"], "name should be VARCHAR"

        usage_schema = conn.execute("DESCRIBE usage").fetchall()
        get_logger.info(f"Usage schema: {usage_schema}")
        usage_schema_dict = {col[0]: col[1] for col in usage_schema}
        assert "VARCHAR" in usage_schema_dict["sql_handle"], "sql_handle should be VARCHAR"
        assert "BIGINT" in usage_schema_dict["execution_count"], "execution_count should be BIGINT"
        assert "BIGINT" in usage_schema_dict["total_rows"], "total_rows should be BIGINT"

        inventory_result = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()
        usage_result = conn.execute("SELECT COUNT(*) FROM usage").fetchone()

        assert inventory_result is not None and inventory_result[0] > 0, "Inventory table should have data"
        assert usage_result is not None and usage_result[0] > 0, "Usage table should have data"
