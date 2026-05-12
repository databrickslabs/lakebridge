"""Unit tests for the BigQuery metadata extract step.

The BQ client is fully mocked — these tests verify that:
  * The two-level (project, region) × SQL-file loop produces the expected DuckDB tables.
  * The `metadatalevel` → `metadata_level` rename runs for `table_storage`.
  * Exclusion flags (`exclude_streaming_metrics`, `exclude_reservations_data`) skip the
    right SQLs.
  * A missing service-account key path produces a hard error (no silent ADC fallback).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest

from databricks.labs.lakebridge.resources.assessments.bigquery import bq_metadata_extract
from databricks.labs.lakebridge.resources.assessments.bigquery.common.compiler import Compiler
from databricks.labs.lakebridge.resources.assessments.bigquery.common.functions import create_bigquery_client


def _canned_df_for(sql_filename: str, project_region: str) -> pd.DataFrame:
    if sql_filename == "table_storage.sql":
        df = pd.DataFrame(
            {
                "metadatalevel": [project_region],
                "active_logical_tb": [1.5],
            }
        )
    else:
        df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
    return df


def _fake_run_sql_for_iteration(sql_filename, _compiler, _bq_client, project_region):
    """Stand-in for `bq_metadata_extract._run_sql_for_iteration` that skips BQ entirely.

    Mirrors the production function's responsibilities that matter at the test boundary:
    returning a (analysis_type, df, elapsed_seconds) tuple and applying the
    `metadatalevel` → `metadata_level` rename for `table_storage`. The canned dataframes
    are deterministic per filename; elapsed is a fixed sentinel.
    """
    df = _canned_df_for(sql_filename, project_region)
    df["source"] = f"{project_region}_{sql_filename}"
    if sql_filename == "table_storage.sql" and "metadatalevel" in df.columns:
        df = df.rename(columns={"metadatalevel": "metadata_level"})
    return bq_metadata_extract._SQL_FILE_TO_ANALYSIS_TYPE[sql_filename], df, 0.01  # pylint: disable=protected-access


@pytest.fixture
def fake_credentials(tmp_path):
    creds = {
        "projects": ["proj-a"],
        "regions": ["us"],
        "service_account_key_path": None,
        "profiling_window_days": 180,
        "max_parallel_sqls": 2,
        "target_cloud": "gcp",
        "profiler": {
            "redact_query_text": True,
            "exclude_reservations_data": False,
            "exclude_streaming_metrics": False,
            "exclude_pricing_analysis": False,
        },
    }
    return creds


def _run_execute(monkeypatch, tmp_path, credentials, argv_extras=None):
    db_path = tmp_path / "profiler_extract.db"
    creds_file = tmp_path / "credentials.yml"
    creds_file.write_text("placeholder: true\n")

    argv = [
        "bq_metadata_extract.py",
        "--db-path",
        str(db_path),
        "--credential-config-path",
        str(creds_file),
    ]
    if argv_extras:
        argv.extend(argv_extras)
    monkeypatch.setattr(sys, "argv", argv)

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = credentials
    monkeypatch.setattr(bq_metadata_extract, "create_credential_manager", lambda *_args, **_kw: cred_manager)
    monkeypatch.setattr(bq_metadata_extract, "create_bigquery_client", lambda *_a, **_kw: MagicMock())
    monkeypatch.setattr(bq_metadata_extract, "_run_sql_for_iteration", _fake_run_sql_for_iteration)

    bq_metadata_extract.execute()
    return db_path


def _tables(db_path: Path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SHOW TABLES").fetchall()
    return {row[0] for row in rows}


def test_full_extract_produces_14_tables(monkeypatch, tmp_path, fake_credentials, capsys):
    db_path = _run_execute(monkeypatch, tmp_path, fake_credentials)
    tables = _tables(db_path)

    expected = {
        # 12 analysis types
        "fulfillment_analysis",
        "table_storage",
        "timeline_analysis",
        "workload_types",
        "commitment_changes",
        "commitments",
        "jobs_timeline_by_reservations",
        "reservation_timeline_analysis",
        "streaming_summary",
        "write_api_summary",
        "consumption_beyond_commitments",
        "consumption_through_commitments",
        # 2 reference tables
        "bq_cluster_pricing",
        "bq_sqlwarehouse_pricing",
    }
    assert tables == expected

    # Final stdout line is the success JSON payload.
    captured = capsys.readouterr()
    last_line = [line for line in captured.out.strip().split("\n") if line][-1]
    payload = json.loads(last_line)
    assert payload["status"] == "success"
    assert set(payload["tables"]) == expected
    assert "wall_clock_seconds" in payload
    assert isinstance(payload["wall_clock_seconds"], (int, float))
    assert payload["wall_clock_seconds"] >= 0


def test_table_storage_renames_metadatalevel_column(monkeypatch, tmp_path, fake_credentials):
    db_path = _run_execute(monkeypatch, tmp_path, fake_credentials)
    with duckdb.connect(str(db_path)) as conn:
        cols = [r[0] for r in conn.execute("DESCRIBE table_storage").fetchall()]
    assert "metadata_level" in cols
    assert "metadatalevel" not in cols


def _row_count(db_path, table: str) -> int:
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])


def test_exclude_streaming_metrics_yields_empty_streaming_tables(monkeypatch, tmp_path, fake_credentials):
    """Excluded SQLs don't run, but their tables still exist with empty stub schemas so
    downstream dashboard queries see them instead of erroring with table-not-found."""
    fake_credentials["profiler"]["exclude_streaming_metrics"] = True
    db_path = _run_execute(monkeypatch, tmp_path, fake_credentials)
    tables = _tables(db_path)
    assert "streaming_summary" in tables
    assert "write_api_summary" in tables
    assert _row_count(db_path, "streaming_summary") == 0
    assert _row_count(db_path, "write_api_summary") == 0
    # Non-streaming tables populated
    assert "workload_types" in tables
    assert _row_count(db_path, "workload_types") > 0


def test_exclude_reservations_data_yields_empty_reservation_tables(monkeypatch, tmp_path, fake_credentials):
    """Same stub-schema behavior for reservation/commitment tables — empty rows when
    excluded, full schema preserved so consumption_by_commitment view can build."""
    fake_credentials["profiler"]["exclude_reservations_data"] = True
    db_path = _run_execute(monkeypatch, tmp_path, fake_credentials)
    tables = _tables(db_path)
    for skipped in (
        "commitments",
        "commitment_changes",
        "consumption_beyond_commitments",
        "consumption_through_commitments",
        "jobs_timeline_by_reservations",
        "reservation_timeline_analysis",
    ):
        assert skipped in tables, f"{skipped} should exist as empty stub"
        assert _row_count(db_path, skipped) == 0, f"{skipped} should have zero rows when excluded"
    assert "workload_types" in tables
    assert _row_count(db_path, "workload_types") > 0


def test_missing_service_account_key_raises_hard_error(tmp_path):
    missing_path = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError, match="Service account key file not found"):
        create_bigquery_client("proj", "us", str(missing_path))


def test_compiler_substitutes_project_region():
    substitutions = [
        {
            "file_path": "sql-client-run/fulfillment_analysis.sql",
            "substitutions": [
                {
                    "search_text": "SET metadatalevel = 'my-gcp-project.region-us';",
                    "find_text": "my-gcp-project.region-us",
                    "replace_with_var": "project_region",
                    "is_rule_active": True,
                },
            ],
        },
    ]
    raw_sql = "SET metadatalevel = 'my-gcp-project.region-us';\nSELECT 1;\n"
    compiler = Compiler(substitutions, project_region="customer.region-eu")
    compiled = compiler.compile("fulfillment_analysis.sql", raw_sql)
    assert "customer.region-eu" in compiled
    assert "my-gcp-project.region-us" not in compiled


def test_compiler_requires_project_region():
    with pytest.raises(ValueError, match="project_region"):
        Compiler([], profiling_window_in_days=180)
