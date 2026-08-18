from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from databricks.labs.lakebridge import __version__ as lakebridge_version
from databricks.labs.lakebridge.assessments import PROFILER_RUN_METADATA_TABLE
from databricks.labs.lakebridge.assessments.pipeline import StepExecutionResult, StepExecutionStatus
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.assessments.run_metadata import (
    PROFILER_RUN_METADATA_SCHEMA,
    ProfilerRunMetadata,
    ProfilerRunStatus,
)


def test_profiler_run_metadata_schema_tracks_dataclass_fields() -> None:
    expected = ", ".join(f"{f.name} {f.metadata['duckdb_type']}" for f in fields(ProfilerRunMetadata))
    assert PROFILER_RUN_METADATA_SCHEMA == expected
    assert [f.name for f in fields(ProfilerRunMetadata)] == [
        "source_system",
        "variant",
        "pipeline_name",
        "pipeline_version",
        "lakebridge_version",
        "python_version",
        "operating_system",
        "status",
        "results",
        "generated_at",
    ]


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], ProfilerRunStatus.COMPLETE.value),
        (
            [StepExecutionResult("a", StepExecutionStatus.COMPLETE)],
            ProfilerRunStatus.COMPLETE.value,
        ),
        (
            [
                StepExecutionResult("a", StepExecutionStatus.COMPLETE),
                StepExecutionResult("b", StepExecutionStatus.ABSENT, "missing"),
            ],
            ProfilerRunStatus.COMPLETE_WITH_ABSENCES.value,
        ),
        (
            [
                StepExecutionResult("a", StepExecutionStatus.ABSENT, "missing"),
                StepExecutionResult("b", StepExecutionStatus.ERROR, "boom"),
            ],
            ProfilerRunStatus.FAILED.value,
        ),
    ],
)
def test_run_status_derivation(results: list[StepExecutionResult], expected: str) -> None:
    assert Profiler._run_status(results) == expected


def test_write_run_metadata_persists_row(tmp_path: Path) -> None:
    db_path = tmp_path / "extract.db"
    pipeline_config = PipelineConfig(name="warehouse_profiler", version="1.2.3", steps=[])
    results = [
        StepExecutionResult("inventory", StepExecutionStatus.COMPLETE),
        StepExecutionResult("optional_metric", StepExecutionStatus.ABSENT, "object missing"),
    ]

    profiler = Profiler("mssql", variant="single_db")
    profiler._write_run_metadata(db_path, pipeline_config, results)

    with duckdb.connect(str(db_path)) as conn:
        columns = [row[0] for row in conn.execute(f"DESCRIBE {PROFILER_RUN_METADATA_TABLE}").fetchall()]
        assert columns == [f.name for f in fields(ProfilerRunMetadata)]

        row = conn.execute(f"SELECT * FROM {PROFILER_RUN_METADATA_TABLE}").fetchone()
        assert row is not None
        (
            source_system,
            variant,
            pipeline_name,
            pipeline_version,
            recorded_version,
            _python_version,
            _operating_system,
            status,
            results_json,
            generated_at,
        ) = row

    assert source_system == "mssql"
    assert variant == "single_db"
    assert pipeline_name == "warehouse_profiler"
    assert pipeline_version == "1.2.3"
    assert recorded_version == lakebridge_version
    assert status == ProfilerRunStatus.COMPLETE_WITH_ABSENCES.value
    assert json.loads(results_json) == [
        {"step_name": "inventory", "status": "COMPLETE", "error_message": None},
        {"step_name": "optional_metric", "status": "ABSENT", "error_message": "object missing"},
    ]
    assert isinstance(generated_at, datetime)
    assert generated_at.tzinfo is not None or generated_at.replace(tzinfo=timezone.utc)


def test_write_run_metadata_overwrites_prior_row(tmp_path: Path) -> None:
    db_path = tmp_path / "extract.db"
    pipeline_config = PipelineConfig(name="warehouse_profiler", version="1.0", steps=[])
    profiler = Profiler("snowflake")

    profiler._write_run_metadata(
        db_path,
        pipeline_config,
        [StepExecutionResult("a", StepExecutionStatus.COMPLETE)],
    )
    profiler._write_run_metadata(
        db_path,
        pipeline_config,
        [StepExecutionResult("b", StepExecutionStatus.ERROR, "failed")],
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(f"SELECT status, results FROM {PROFILER_RUN_METADATA_TABLE}").fetchall()

    assert len(rows) == 1
    status, results_json = rows[0]
    assert status == ProfilerRunStatus.FAILED.value
    assert json.loads(results_json) == [
        {"step_name": "b", "status": "ERROR", "error_message": "failed"},
    ]


def test_normalize_source_system_casefolds_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        profiler = Profiler("MSSQL")
    assert profiler._source_system == "mssql"

    with caplog.at_level("WARNING"):
        Profiler("not_a_real_source")
    assert "Unknown source system" in caplog.text
