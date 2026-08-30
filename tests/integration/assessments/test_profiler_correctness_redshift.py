"""Correctness integration test for the Redshift (provisioned) profiler.

Where the generic pipeline tests only prove the engine runs, this test runs the
*real* Redshift provisioned profiler queries
(``resources/assessments/redshift/provisioned/``) against the live Redshift
sandbox and validates the shape of the resulting DuckDB extract:

* every profiler query executes without error against the real system tables;
* the extract contains only expected tables (no stray tables);
* each produced table has exactly the expected set of columns;
* the tables backed by unconditional aggregates are always produced (they can
  never be legitimately empty), which -- because ``_save_to_db`` skips table
  creation for a zero-row result -- also asserts they were populated.

Column *types* are intentionally not asserted here: they depend on the live
data returned by the Redshift system tables and cannot be pinned deterministically
without golden/seeded data. Type-level fidelity is covered separately by the
unit tests.

Requires Redshift sandbox credentials (``REDSHIFT_*`` in
``~/.databricks/debug-env.json`` or the environment). The whole module is skipped
when they are absent, so it only runs in CI where the secrets are configured.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.pipeline import (
    PipelineClass,
    StepExecutionStatus,
    make_profiler_db_filename,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

from tests.integration.assessments.profiler_extract_helpers import env_available

_REDSHIFT_ENV_KEYS = ("REDSHIFT_HOST", "REDSHIFT_USER", "REDSHIFT_PASS", "REDSHIFT_PORT")

_PROVISIONED_CONFIG = Path(
    "src/databricks/labs/lakebridge/resources/assessments/redshift/provisioned/pipeline_config.yml"
)

# Expected output columns for each DuckDB table the provisioned profiler produces.
# Table names are the pipeline step names; columns are the SELECT aliases in the
# corresponding <step>.sql. Compared case-insensitively (the driver may change
# identifier casing).
_EXPECTED_COLUMNS: dict[str, frozenset[str]] = {
    "rs_spectrum_tb_month": frozenset({"set_name", "s3_scanned_tb_month", "avg_daily_scanned_tb"}),
    "rs_managed_storage_gb": frozenset({"set_name", "rs_managed_storage_gb"}),
    "rs_nodes": frozenset({"set_name", "rs_nodes_type", "rs_number_of_nodes"}),
    "rs_avg_concurrent_users": frozenset({"set_name", "avg_concurrent_users"}),
    "rs_avg_queries_minute": frozenset({"set_name", "avg_queries_minute"}),
    "chart_query_type_by_hour": frozenset({"set_name", "query_type", "hour", "count"}),
    "chart_cpu_consumption_by_query_type": frozenset({"set_name", "query_type", "sum_cpu_time"}),
    "chart_concurrent_users_by_hour": frozenset({"set_name", "distinct_users", "hour"}),
    "chart_cpu_consumption_by_hour_and_query_type": frozenset({"set_name", "query_type", "sum_cpu_time", "hour"}),
}

# Tables whose query is an unconditional aggregate (no outer WHERE / GROUP BY), so
# it always returns exactly one row and the table is therefore always created.
# The remaining tables (spectrum + charts) are data-dependent and may be absent
# when the sandbox has no matching activity.
_ALWAYS_PRESENT: frozenset[str] = frozenset(
    {"rs_managed_storage_gb", "rs_avg_concurrent_users", "rs_avg_queries_minute"}
)


pytestmark = pytest.mark.skipif(
    not env_available(_REDSHIFT_ENV_KEYS),
    reason="Redshift sandbox credentials not configured (REDSHIFT_* in debug-env)",
)


def _created_tables(db_path: Path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SHOW TABLES").fetchall()}


def _column_names(db_path: Path, table: str) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table],
        ).fetchall()
    return {row[0].lower() for row in rows}


def test_redshift_provisioned_profiler_extract_is_correct(
    sandbox_redshift: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    config = Profiler.path_modifier(config_file=project_path / _PROVISIONED_CONFIG, path_prefix=project_path)
    db_path = tmp_path / make_profiler_db_filename("redshift")

    results = PipelineClass(config, sandbox_redshift, db_path, tmp_path / "credentials.yml").execute()

    # Every real profiler query ran against the live system tables without error.
    for result in results:
        assert result.status in (
            StepExecutionStatus.COMPLETE,
            StepExecutionStatus.SKIPPED,
        ), f"Step {result.step_name} failed: {result.error_message}"

    created = _created_tables(db_path)

    # No stray tables, and the always-produced ones are present (hence populated).
    assert created <= set(_EXPECTED_COLUMNS), f"Unexpected tables in extract: {created - set(_EXPECTED_COLUMNS)}"
    assert _ALWAYS_PRESENT <= created, f"Expected always-present tables missing: {_ALWAYS_PRESENT - created}"

    # Every produced table has exactly the expected columns.
    for table in created:
        expected = {col.lower() for col in _EXPECTED_COLUMNS[table]}
        assert _column_names(db_path, table) == expected, f"Column mismatch for table '{table}'"
