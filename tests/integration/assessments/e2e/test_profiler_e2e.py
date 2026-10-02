"""Profiler live-database e2e tests — drive the REAL profiler through the production CLI entry points.

For every source we exercise the two commands a real user runs, calling the exact CLI functions
in-process (the same pattern as ``tests/integration/cli/test_profiler_connection_cli.py``) so the
test path *is* the production path — argument validation, credential resolution, connector/pipeline
construction, and the DuckDB extract + ``profiler_run_metadata`` write:

  * ``databricks labs lakebridge test-profiler-connection``  -> ``cli.test_profiler_connection``
  * ``databricks labs lakebridge execute-database-profiler`` -> ``cli.execute_database_profiler``

Credentials are downloaded by ``setup_e2e_creds.sh`` from the UC volume to the default lakebridge
path; the ``ws`` fixture uses the same ambient Databricks auth. Gating:

  * the whole suite skips when the default ``.credentials.yml`` is absent;
  * Snowflake and BigQuery are opt-in via ``LAKEBRIDGE_E2E_SNOWFLAKE`` / ``LAKEBRIDGE_E2E_BIGQUERY``
    — running them is governed by contract/competition terms, not by technical setup, so they never
    run implicitly. Set the flag (and provide the key material) to include them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.assessments import PROFILER_RUN_METADATA_TABLE
from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    StepExecutionStatus,
    make_profiler_db_filename,
)
from databricks.labs.lakebridge.assessments.profiler import get_pipeline
from databricks.labs.lakebridge.assessments.profiler_config import Step
from databricks.labs.lakebridge.assessments.run_metadata import ProfilerRunStatus
from databricks.labs.lakebridge.assessments.variants import resolve_variant
from databricks.labs.lakebridge.connections.credential_manager import cred_file

_CREDS_FILE = cred_file("lakebridge")

# The sources that always run once credentials are present.
_ALWAYS_SOURCES = ["redshift", "synapse", "legacy_synapse", "oracle", "teradata"]

skip_no_creds = pytest.mark.skipif(
    not _CREDS_FILE.is_file(),
    reason=f"no e2e credentials at {_CREDS_FILE}; run setup_e2e_creds.sh first",
)
# Snowflake + BigQuery are opt-in: running them against live accounts is governed by contract terms,
# not technical setup. Flip the flag (and put the key material in the volume) to include them.
skip_snowflake = pytest.mark.skipif(
    not os.environ.get("LAKEBRIDGE_E2E_SNOWFLAKE"),
    reason="snowflake e2e is opt-in; set LAKEBRIDGE_E2E_SNOWFLAKE=1 to run it",
)
skip_bigquery = pytest.mark.skipif(
    not os.environ.get("LAKEBRIDGE_E2E_BIGQUERY"),
    reason="bigquery e2e is opt-in (contract terms); set LAKEBRIDGE_E2E_BIGQUERY=1 to run it",
)


def _pipeline_steps(source: str) -> list[Step]:
    """The pipeline steps the run used, so we can tell active / required / SQL steps apart.

    None of the seven e2e sources declares a variant (only ``mssql`` does), so ``resolve_variant``
    returns ``None`` without a live probe and ``get_pipeline`` points at the same installed config
    the profiler just executed.
    """
    variant = resolve_variant(source, None, cred_file_path=_CREDS_FILE)
    return PipelineClass.load_config_from_yaml(get_pipeline(source, variant)).steps


def _assert_profiler_extract(output_folder: Path, source: str) -> None:
    """Assert the real production output: the DuckDB extract, its run-metadata row, the per-step
    outcomes it recorded, and that every active SQL step produced its table.

    Two behaviours are load-bearing here:
      * a live run may finish COMPLETE_WITH_ABSENCES (an optional source object was missing), so both
        terminal-success statuses pass and only ERROR (FAILED) fails the test;
      * a step may legitimately return zero rows -- the pipeline still creates the typed table and
        skips the insert -- so we assert each expected table *exists*, never that it is non-empty
        (non-emptiness is account-dependent and would be flaky).

    Column-level DDL alignment is covered offline, without credentials, by
    ``tests/unit/assessment/test_profiler_ddl.py``; here we assert the live run produced those tables.
    """
    db_path = output_folder / make_profiler_db_filename(source)
    assert db_path.exists(), f"{source}: profiler extract {db_path} was not created"

    with duckdb.connect(str(db_path), read_only=True) as conn:
        meta = conn.execute(f"SELECT source_system, status, results FROM {PROFILER_RUN_METADATA_TABLE}").fetchone()
        table_names = {
            name.casefold()
            for (name,) in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }

    assert meta is not None, f"{source}: no {PROFILER_RUN_METADATA_TABLE} row was written"
    source_system, status, results_json = meta
    assert source_system == source, f"expected source_system={source!r}, got {source_system!r}"
    assert status in (
        ProfilerRunStatus.COMPLETE.value,
        ProfilerRunStatus.COMPLETE_WITH_ABSENCES.value,
    ), f"{source}: profiler run status was {status}; step results: {results_json}"

    # Per-step outcomes the run recorded in profiler_run_metadata.results.
    step_results = json.loads(results_json)
    assert step_results, f"{source}: run metadata recorded no step results"
    status_by_step = {s["step_name"]: s["status"] for s in step_results}
    errored = sorted(name for name, st in status_by_step.items() if st == StepExecutionStatus.ERROR.value)
    assert not errored, f"{source}: steps reported ERROR: {errored}; results: {results_json}"

    # Cross-check the config the run used: every active required (non-optional) step must be COMPLETE,
    # and every active SQL step must have produced its table (empty is fine; missing is not).
    for step in _pipeline_steps(source):
        if step.flag != "active":
            continue
        if not step.optional:
            assert status_by_step.get(step.name) == StepExecutionStatus.COMPLETE.value, (
                f"{source}: required step {step.name!r} was {status_by_step.get(step.name)!r}, "
                f"expected COMPLETE; results: {results_json}"
            )
        if step.type == "sql":
            assert (
                step.name.casefold() in table_names
            ), f"{source}: active SQL step {step.name!r} produced no table in {db_path.name}"


# --- test-profiler-connection -----------------------------------------------------------------


@skip_no_creds
@pytest.mark.parametrize("source", _ALWAYS_SOURCES)
def test_connection(ws: WorkspaceClient, source: str) -> None:
    """`test-profiler-connection` validates connectivity through the production code path."""
    cli.test_profiler_connection(w=ws, source_tech=source, cred_file_path=str(_CREDS_FILE))


@skip_no_creds
@skip_snowflake
def test_connection_snowflake(ws: WorkspaceClient) -> None:
    cli.test_profiler_connection(w=ws, source_tech="snowflake", cred_file_path=str(_CREDS_FILE))


@skip_no_creds
@skip_bigquery
def test_connection_bigquery(ws: WorkspaceClient) -> None:
    cli.test_profiler_connection(w=ws, source_tech="bigquery", cred_file_path=str(_CREDS_FILE))


# --- execute-database-profiler ----------------------------------------------------------------


@skip_no_creds
@pytest.mark.parametrize("source", _ALWAYS_SOURCES)
def test_profile(ws: WorkspaceClient, source: str, tmp_path: Path) -> None:
    """`execute-database-profiler` runs the real pipeline and writes the DuckDB extract."""
    cli.execute_database_profiler(
        w=ws, source_tech=source, output_folder=str(tmp_path), cred_file_path=str(_CREDS_FILE)
    )
    _assert_profiler_extract(tmp_path, source)


@skip_no_creds
@skip_snowflake
def test_profile_snowflake(ws: WorkspaceClient, tmp_path: Path) -> None:
    cli.execute_database_profiler(
        w=ws, source_tech="snowflake", output_folder=str(tmp_path), cred_file_path=str(_CREDS_FILE)
    )
    _assert_profiler_extract(tmp_path, "snowflake")


@skip_no_creds
@skip_bigquery
def test_profile_bigquery(ws: WorkspaceClient, tmp_path: Path) -> None:
    cli.execute_database_profiler(
        w=ws, source_tech="bigquery", output_folder=str(tmp_path), cred_file_path=str(_CREDS_FILE)
    )
    _assert_profiler_extract(tmp_path, "bigquery")
