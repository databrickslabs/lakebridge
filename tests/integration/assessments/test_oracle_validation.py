"""
Oracle profiler extract validation tests.

These build a mock Oracle extract from the *shipped* Oracle DDL resources and validate it against the
*shipped* ``oracle_extract_schema.yml``. They therefore guard two things at once: that the validation
schema definition stays consistent with the extract DDL, and that the validator accepts a conformant
Oracle extract. They are pure-DuckDB (no Spark / workspace required).
"""

import tempfile
from collections.abc import Generator
from importlib import resources
from pathlib import Path

import duckdb
import pytest

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.profiler_validator import (
    EmptyTableValidationCheck,
    ExtractSchemaValidationCheck,
    build_validation_report,
)
from .profiler_extract_utils import build_mock_oracle_extract

ORACLE_TABLES = [
    "config_containers",
    "config_db_features",
    "config_instance",
    "config_memory_evolution",
    "config_pdb_objects",
    "config_pdb_partitions",
    "config_storage",
    "perf_cpu_waits",
    "perf_fgd_session_evolution",
    "perf_heatmap",
    "perf_sqltext",
]


@pytest.fixture(scope="session")
def mock_oracle_profiler_extract() -> Generator[Path]:
    with tempfile.TemporaryDirectory(prefix="lakebridge_test_") as temp_dir:
        extract_dir = Path(temp_dir) / "oracle_assessment"
        yield build_mock_oracle_extract("mock_oracle_extract", path_prefix=extract_dir)


def _oracle_schema_def() -> resources.abc.Traversable:
    return resources.files(assessment_resources).joinpath("validation/oracle_extract_schema.yml")


def test_oracle_extract_matches_shipped_schema(mock_oracle_profiler_extract: Path) -> None:
    """Every Oracle table created from the shipped DDL conforms to the shipped schema definition."""
    with (
        resources.as_file(_oracle_schema_def()) as schema_path,
        duckdb.connect(database=mock_oracle_profiler_extract) as duck_conn,
    ):
        checks = [
            ExtractSchemaValidationCheck(
                "main",
                table,
                source_tech="oracle",
                extract_path=str(mock_oracle_profiler_extract),
                schema_path=str(schema_path),
            )
            for table in ORACLE_TABLES
        ]
        report = build_validation_report(checks, duck_conn)

    failures = [r for r in report if r.outcome == "FAIL"]
    assert not failures, f"Unexpected schema validation failures: {failures}"
    assert len(report) == len(ORACLE_TABLES)


def test_oracle_tables_are_not_empty(mock_oracle_profiler_extract: Path) -> None:
    with duckdb.connect(database=mock_oracle_profiler_extract) as duck_conn:
        checks = [EmptyTableValidationCheck(f"main.{table}") for table in ORACLE_TABLES]
        report = build_validation_report(checks, duck_conn)
    assert all(r.outcome == "PASS" for r in report), [r for r in report if r.outcome != "PASS"]


def test_oracle_schema_mismatch_is_detected() -> None:
    """A column whose type drifts from the schema definition must be reported as FAIL."""
    with tempfile.TemporaryDirectory(prefix="lakebridge_test_") as temp_dir:
        extract = build_mock_oracle_extract("mock_oracle_mismatch", path_prefix=Path(temp_dir) / "oracle_assessment")
        with (
            resources.as_file(_oracle_schema_def()) as schema_path,
            duckdb.connect(database=extract) as duck_conn,
        ):
            # config_instance.inst_id is INTEGER in the schema definition; force a mismatch.
            duck_conn.execute("ALTER TABLE config_instance ALTER inst_id TYPE VARCHAR")
            check = ExtractSchemaValidationCheck(
                "main",
                "config_instance",
                source_tech="oracle",
                extract_path=str(extract),
                schema_path=str(schema_path),
            )
            report = build_validation_report([check], duck_conn)

    assert len(report) == 1
    assert report[0].outcome == "FAIL"
    assert report[0].summary == "Unexpected column data type"
