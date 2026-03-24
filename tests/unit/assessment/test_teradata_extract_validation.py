"""Tests for Teradata profiler extract validation and ingestion logic."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.dashboards import execute as dashboard_execute
from databricks.labs.lakebridge.assessments.profiler_validator import (
    EmptyTableValidationCheck,
    ExtractSchemaValidationCheck,
    build_validation_report,
)
from tests.unit.spark_test_stubs import SparkSessionStub
from tests.unit.teradata_test_helpers import TERADATA_TABLES

from importlib import resources

import databricks.labs.lakebridge.resources.assessments as assessment_resources

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


def test_ingest_teradata_tables_ingests_all(teradata_extract: Path, monkeypatch) -> None:
    """_ingest_profiler_tables should ingest all 10 Teradata tables."""
    spark_stub = SparkSessionStub()

    class _BuilderStub:
        @staticmethod
        def getOrCreate() -> SparkSessionStub:  # noqa: N802
            return spark_stub

    monkeypatch.setattr(dashboard_execute.SparkSession, "builder", _BuilderStub())

    dashboard_execute._ingest_profiler_tables("test_catalog", "test_schema", str(teradata_extract))

    saved_tables = {item[1].saved_table for item in spark_stub.ingested if item[1].saved_table}
    for table in TERADATA_TABLES:
        expected_uc_name = f"test_catalog.test_schema.{table}"
        assert expected_uc_name in saved_tables, f"Table not ingested: {expected_uc_name}"


def test_ingest_teradata_table_preserves_null_database_in_udf(teradata_extract: Path, monkeypatch) -> None:
    """UDFs with NULL DatabaseName should be ingested without errors."""
    spark_stub = SparkSessionStub()

    class _BuilderStub:
        @staticmethod
        def getOrCreate() -> SparkSessionStub:  # noqa: N802
            return spark_stub

    monkeypatch.setattr(dashboard_execute.SparkSession, "builder", _BuilderStub())

    dashboard_execute._ingest_table(
        extract_location=str(teradata_extract),
        source_table_name="main.td_dwh_udf",
        target_table_name="cat.schema.td_dwh_udf",
    )

    pdf = spark_stub.ingested[0][0]
    assert len(pdf) == 3
    assert pdf["DatabaseName"].isna().sum() == 1
