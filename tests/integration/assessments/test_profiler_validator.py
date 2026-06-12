import dataclasses
import tempfile
from collections.abc import Generator
from pathlib import Path

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.profiler_validator import (
    EmptyTableValidationCheck,
    build_validation_report,
    NullValidationCheck,
    ExtractSchemaValidationCheck,
    SchemaDefinitionLoadError,
    SchemaValidationError,
)
from databricks.labs.lakebridge.assessments import source_system_family

from .profiler_extract_utils import build_mock_synapse_extract, build_mock_redshift_extract

# Platform-specific config for parametrized validator tests (same test logic, different tables/counts)


@dataclasses.dataclass
class _PlatformValidatorConfig:
    schema_file: str
    nonexistent_schema_file: str
    schema_path_check_table: str
    success_schema_table: str
    invalid_schema_table: str
    expected_non_empty_total: int
    expected_non_empty_fail: int
    expected_non_empty_pass: int
    mixed_table_1: str
    mixed_table_2: str
    mixed_null_table: str
    mixed_null_cols: list[str]
    expected_mixed_total: int


PLATFORM_VALIDATOR_CONFIG: dict[str, _PlatformValidatorConfig] = {
    "synapse": _PlatformValidatorConfig(
        schema_file="synapse_schema_def.yml",
        nonexistent_schema_file="synapse_scheme_def_nonexists.yml",
        schema_path_check_table="dedicated_routines",
        success_schema_table="dedicated_sql_pool_metrics",
        invalid_schema_table="dedicated_storage_info",
        expected_non_empty_total=3,
        expected_non_empty_fail=1,
        expected_non_empty_pass=2,
        mixed_table_1="mock_profiler_extract.main.dedicated_sql_pool_metrics",
        mixed_table_2="mock_profiler_extract.main.workspace_sql_pools",
        mixed_null_table="mock_profiler_extract.main.workspace_sql_pools",
        mixed_null_cols=["id", "sku"],
        expected_mixed_total=4,
    ),
    "redshift_provisioned": _PlatformValidatorConfig(
        schema_file="redshift_schema_def.yml",
        nonexistent_schema_file="redshift_scheme_def_nonexists.yml",
        schema_path_check_table="query_view",
        success_schema_table="query_view",
        invalid_schema_table="rs_managed_storage_gb",
        expected_non_empty_total=3,
        expected_non_empty_fail=1,
        expected_non_empty_pass=2,
        mixed_table_1="mock_profiler_extract.main.query_view",
        mixed_table_2="mock_profiler_extract.main.rs_managed_storage_gb",
        mixed_null_table="mock_profiler_extract.main.query_view",
        mixed_null_cols=["user_id", "query_id"],
        expected_mixed_total=4,
    ),
}


@pytest.fixture(scope="session")
def mock_synapse_profiler_extract() -> Generator[Path]:
    # We don't use tmp_path because this is quite expensive to set up.
    # Use context manager for automatic cleanup
    with tempfile.TemporaryDirectory(prefix="lakebridge_test_") as temp_dir:
        extract_dir = Path(temp_dir) / "synapse_assessment"
        synapse_extract_path = build_mock_synapse_extract("mock_profiler_extract", path_prefix=extract_dir)
        yield synapse_extract_path


@pytest.fixture(scope="session")
def mock_redshift_profiler_extract() -> Generator[Path]:
    with tempfile.TemporaryDirectory(prefix="lakebridge_test_") as temp_dir:
        extract_dir = Path(temp_dir) / "redshift_assessment"
        redshift_extract_path = build_mock_redshift_extract("mock_profiler_extract", path_prefix=extract_dir)
        yield redshift_extract_path


def _get_mock_extract(platform: str, mock_synapse: Path, mock_redshift: Path) -> Path:
    if platform == "synapse":
        return mock_synapse
    if platform == "redshift_provisioned":
        return mock_redshift
    raise ValueError(f"Unknown platform: {platform}")


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_non_empty_tables(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = []
        tables = duck_conn.execute("SHOW ALL TABLES").fetchall()
        for table in tables:
            fq_table_name = f"{table[0]}.{table[1]}.{table[2]}"
            empty_check = EmptyTableValidationCheck(fq_table_name)
            validation_checks.append(empty_check)
        report = build_validation_report(validation_checks, duck_conn)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == cfg.expected_non_empty_total
        assert num_failures == cfg.expected_non_empty_fail
        assert num_passing == cfg.expected_non_empty_pass


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_mixed_checks(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    table_1, table_2 = cfg.mixed_table_1, cfg.mixed_table_2
    null_table, null_cols = cfg.mixed_null_table, cfg.mixed_null_cols
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            EmptyTableValidationCheck(table_1, "ERROR"),
            EmptyTableValidationCheck(table_2, "ERROR"),
            NullValidationCheck(null_table, null_cols[0], "ERROR"),
            NullValidationCheck(null_table, null_cols[1], "WARN"),
        ]
        report = build_validation_report(validation_checks, duck_conn)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == cfg.expected_mixed_total
        assert num_failures == 0
        assert num_passing == cfg.expected_mixed_total


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_invalid_schema_path(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
    test_resources: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    schema_def_path = test_resources / "assessments" / cfg.nonexistent_schema_file
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            ExtractSchemaValidationCheck(
                "main",
                cfg.schema_path_check_table,
                source_tech=source_system_family(platform),
                extract_path=str(extract_path),
                schema_path=str(schema_def_path),
            )
        ]
        with pytest.raises(SchemaDefinitionLoadError) as exec_info:
            build_validation_report(validation_checks, duck_conn)
        assert "Schema definition file not found:" in str(exec_info.value)


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_invalid_source_tech(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
    test_resources: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    schema_def_path = test_resources / "assessments" / cfg.schema_file
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            ExtractSchemaValidationCheck(
                "main",
                cfg.schema_path_check_table,
                source_tech="oracle",
                extract_path=str(extract_path),
                schema_path=str(schema_def_path),
            )
        ]
        with pytest.raises(AssertionError) as exec_info:
            build_validation_report(validation_checks, duck_conn)
        assert "Incorrect schema definition type for source tech" in str(exec_info.value)


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_table_not_found(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
    test_resources: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    schema_def_path = test_resources / "assessments" / cfg.schema_file
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            ExtractSchemaValidationCheck(
                "main",
                "table_does_not_exist",
                source_tech=source_system_family(platform),
                extract_path=str(extract_path),
                schema_path=str(schema_def_path),
            )
        ]
        with pytest.raises(SchemaValidationError) as exec_info:
            build_validation_report(validation_checks, duck_conn)
        assert "could not be found" in str(exec_info.value)


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_successful_schema_check(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
    test_resources: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    schema_def_path = test_resources / "assessments" / cfg.schema_file
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            ExtractSchemaValidationCheck(
                "main",
                cfg.success_schema_table,
                source_tech=source_system_family(platform),
                extract_path=str(extract_path),
                schema_path=str(schema_def_path),
            )
        ]
        report = build_validation_report(validation_checks, duck_conn)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == 1
        assert num_failures == 0
        assert num_passing == 1


@pytest.mark.parametrize("platform", ["synapse", "redshift_provisioned"])
def test_validate_invalid_schema_check(
    platform: str,
    mock_synapse_profiler_extract: Path,
    mock_redshift_profiler_extract: Path,
    test_resources: Path,
) -> None:
    extract_path = _get_mock_extract(platform, mock_synapse_profiler_extract, mock_redshift_profiler_extract)
    cfg = PLATFORM_VALIDATOR_CONFIG[platform]
    schema_def_path = test_resources / "assessments" / cfg.schema_file
    with duckdb.connect(database=extract_path) as duck_conn:
        validation_checks = [
            ExtractSchemaValidationCheck(
                "main",
                cfg.invalid_schema_table,
                source_tech=source_system_family(platform),
                extract_path=str(extract_path),
                schema_path=str(schema_def_path),
            )
        ]
        report = build_validation_report(validation_checks, duck_conn)
        num_failures = len(list(filter(lambda row: row.outcome == "FAIL", report)))
        num_passing = len(list(filter(lambda row: row.outcome == "PASS", report)))
        assert len(report) == 1
        assert num_failures == 1
        assert num_passing == 0
        assert report[0].summary == "Unexpected column data type"
