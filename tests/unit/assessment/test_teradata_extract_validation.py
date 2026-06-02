"""Tests for Teradata profiler extract validation logic."""

from __future__ import annotations

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
from tests.unit.teradata_test_helpers import TERADATA_TABLES

from .teradata_extract_utils import build_mock_teradata_extract


@pytest.fixture()
def teradata_extract(tmp_path: Path) -> Path:
    return build_mock_teradata_extract(tmp_path / "profiler_extract.db")


@pytest.fixture()
def teradata_schema_def_path() -> Path:
    root = resources.files(assessment_resources)
    schema_def = root.joinpath("validation").joinpath("teradata_extract_schema.yml")
    assert schema_def.is_file(), "teradata_extract_schema.yml must exist"
    with resources.as_file(schema_def) as schema_path:
        return Path(schema_path)


def test_teradata_extract_has_all_expected_tables(teradata_extract: Path) -> None:
    """All 10 Teradata profiler tables should exist in the mock extract."""
    with duckdb.connect(str(teradata_extract)) as conn:
        tables = conn.execute("SHOW ALL TABLES").fetchall()
        table_names = {row[2] for row in tables}
    for expected in TERADATA_TABLES:
        assert expected in table_names, f"Missing table: {expected}"


def test_teradata_extract_tables_are_non_empty(teradata_extract: Path) -> None:
    """Every table in the mock extract should have at least one row."""
    with duckdb.connect(str(teradata_extract)) as conn:
        validation_checks = []
        for table in TERADATA_TABLES:
            validation_checks.append(EmptyTableValidationCheck(f"main.{table}"))
        report = build_validation_report(validation_checks, conn)

    failures = [r for r in report if r.outcome == "FAIL"]
    assert len(failures) == 0, f"Tables with no rows: {[f.table for f in failures]}"


def test_teradata_extract_schema_matches_definition(teradata_extract: Path, teradata_schema_def_path: Path) -> None:
    """Every table should match the column types defined in teradata_extract_schema.yml."""
    with duckdb.connect(str(teradata_extract)) as conn:
        validation_checks = []
        for table in TERADATA_TABLES:
            check = ExtractSchemaValidationCheck(
                "main",
                table,
                source_tech="teradata",
                extract_path=str(teradata_extract),
                schema_path=str(teradata_schema_def_path),
            )
            validation_checks.append(check)
        report = build_validation_report(validation_checks, conn)

    failures = [r for r in report if r.outcome == "FAIL"]
    assert len(failures) == 0, f"Schema mismatches: {[(f.table, f.summary) for f in failures]}"


def test_teradata_extract_schema_wrong_source_tech_rejected(
    teradata_extract: Path, teradata_schema_def_path: Path
) -> None:
    """Using a mismatched source_tech against teradata schema should fail."""
    with duckdb.connect(str(teradata_extract)) as conn:
        check = ExtractSchemaValidationCheck(
            "main",
            "td_sys_info",
            source_tech="synapse",
            extract_path=str(teradata_extract),
            schema_path=str(teradata_schema_def_path),
        )
        with pytest.raises(AssertionError, match="Incorrect schema definition type"):
            build_validation_report([check], conn)
