from pathlib import Path
import pytest
import duckdb

from databricks.labs.lakebridge.assessments.profiler_validator import (
    get_profiler_extract_path,
    EmptyTableValidationCheck,
    build_validation_report,
    NullValidationCheck,
)
from .utils.profiler_extract_utils import build_mock_synapse_extract


@pytest.fixture(scope="module")
def pipeline_config_path():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config.yml"
    return config_path


@pytest.fixture(scope="module")
def failure_pipeline_config_path():
    prefix = Path(__file__).parent
    config_path = f"{prefix}/../../resources/assessments/pipeline_config_python_failure.yml"
    return config_path


@pytest.fixture(scope="module")
def mock_synapse_profiler_extract():
    synapse_extract_path = build_mock_synapse_extract("mock_profiler_extract")
    return synapse_extract_path


def test_get_profiler_extract_path(pipeline_config_path, failure_pipeline_config_path):
    # Parse `extract_folder` **with** a trailing "/" character
    expected_db_path = "/tmp/extracts/profiler_extract.db"
    profiler_db_path = get_profiler_extract_path(pipeline_config_path)
    assert profiler_db_path == expected_db_path

    # Parse `extract_folder` **without** a trailing "/" character
    expected_db_path = "tests/resources/assessments/profiler_extract.db"
    profiler_db_path = get_profiler_extract_path(failure_pipeline_config_path)
    assert profiler_db_path == expected_db_path


def test_validate_non_empty_tables(mock_synapse_profiler_extract):
    with duckdb.connect(database=mock_synapse_profiler_extract) as duck_conn:
        validation_checks = []
        # Get a list of all tables in profiler extract and add an EmptyTableValidationCheck
        # Alternatively, this can be a pre-defined list (following test case)
        tables = duck_conn.execute("SHOW ALL TABLES").fetchall()
        for table in tables:
            fq_table_name = f"{table[0]}.{table[1]}.{table[2]}"
            empty_check = EmptyTableValidationCheck(fq_table_name)
            validation_checks.append(empty_check)
        report = build_validation_report(validation_checks, duck_conn)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == 3
        assert num_failures == 1
        assert num_passing == 2


def test_validate_mixed_checks(mock_synapse_profiler_extract):
    table_1 = "mock_profiler_extract.main.dedicated_sql_pool_metrics"
    table_2 = "mock_profiler_extract.main.workspace_sql_pools"
    with duckdb.connect(database=mock_synapse_profiler_extract) as duck_conn:
        validation_checks = [
            EmptyTableValidationCheck(table_1, "ERROR"),  # override default severity level
            EmptyTableValidationCheck(table_2, "ERROR"),
            NullValidationCheck(table_2, "id", "ERROR"),
            NullValidationCheck(table_2, "sku", "WARN"),
        ]
        report = build_validation_report(validation_checks, duck_conn)
        print(report)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == 4
        assert num_failures == 0
        assert num_passing == 4
