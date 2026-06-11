import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest

from databricks.labs.lakebridge.resources.assessments.bigquery import bq_metadata_extract
from databricks.labs.lakebridge.resources.assessments.common.sql_substituter import substitute


def _fake_run_sql_for_iteration(sql_filename, _substitution_vars, _bq_client, project_region):
    df = pd.DataFrame(
        {"metadata_level": [project_region], "active_logical_tb": [1.5], "source": f"{project_region}_{sql_filename}"}
    )
    return df, 0.01


@pytest.fixture
def fake_credentials(tmp_path):
    creds = {
        "pairs": [{"project": "proj-a", "region": "us"}],
        "profiler": {
            "profiling_window_days": 180,
            "max_parallel_sqls": 2,
            "redact_query_text": True,
            "exclude_reservations_data": False,
            "exclude_streaming_metrics": False,
        },
    }
    return creds


def _run_execute(monkeypatch, tmp_path, credentials):
    db_path = tmp_path / "profiler_extract.db"

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = credentials
    monkeypatch.setattr(bq_metadata_extract, "_run_sql_for_iteration", _fake_run_sql_for_iteration)

    bq_metadata_extract.execute(
        credential_manager=cred_manager,
        bigquery_client_factory=lambda *_a, **_kw: MagicMock(),
        db_path=str(db_path),
    )
    return db_path


def _tables(db_path: Path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SHOW TABLES").fetchall()
    return {row[0] for row in rows}


def test_full_extract_produces_12_tables(monkeypatch, tmp_path, fake_credentials, capsys):
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
    excluded, full schema preserved so downstream consumers see consistent tables."""
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


def test_substitute_fills_placeholders():
    raw_sql = (
        "SELECT '{{project_region}}' AS metadata_level\n"
        "FROM `{{project_region}}`.INFORMATION_SCHEMA.JOBS\n"
        "WHERE DATE(creation_time) > DATE_SUB(CURRENT_DATE(), INTERVAL {{profiling_window_in_days}} DAY)\n"
    )
    compiled = substitute(raw_sql, {"project_region": "customer.region-eu", "profiling_window_in_days": 180})
    assert "customer.region-eu" in compiled
    assert "INTERVAL 180 DAY" in compiled
    assert "{{" not in compiled


def test_substitute_raises_on_unfilled_placeholder():
    # A placeholder with no matching variable must fail loudly, never reach BigQuery as `{{...}}`.
    with pytest.raises(ValueError, match="project_region"):
        substitute("SELECT '{{project_region}}' AS metadata_level", {})


def test_one_pair_failure_does_not_abort_others(monkeypatch, tmp_path, fake_credentials, capsys):
    """A single failing (project, region) is soft-failed: the loop logs and continues, the
    healthy pair's data is still written, and the final payload reports per-pair status."""
    fake_credentials["pairs"] = [
        {"project": "proj-good", "region": "us"},
        {"project": "proj-bad", "region": "eu"},
    ]
    db_path = tmp_path / "profiler_extract.db"
    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = fake_credentials

    def _selective(sql_filename, substitution_vars, bq_client, project_region):
        if "proj-bad" in project_region:
            raise RuntimeError(f"simulated failure for {project_region}")
        return _fake_run_sql_for_iteration(sql_filename, substitution_vars, bq_client, project_region)

    monkeypatch.setattr(bq_metadata_extract, "_run_sql_for_iteration", _selective)
    bq_metadata_extract.execute(
        credential_manager=cred_manager,
        bigquery_client_factory=lambda *_a, **_kw: MagicMock(),
        db_path=str(db_path),
    )

    # The healthy pair's data survives the other pair's failure.
    assert _row_count(db_path, "workload_types") > 0

    payload = json.loads([line for line in capsys.readouterr().out.strip().split("\n") if line][-1])
    assert payload["status"] == "success"
    statuses = {(p["project"], p["region"]): p["status"] for p in payload["pairs"]}
    assert statuses == {("proj-good", "us"): "success", ("proj-bad", "eu"): "error"}
    bad = next(p for p in payload["pairs"] if p["status"] == "error")
    assert "simulated failure" in bad["message"]


def test_all_pairs_failing_reports_top_level_error(monkeypatch, tmp_path, fake_credentials, capsys):
    """If every pair fails there is nothing to salvage, so the step exits non-zero with a
    structured error payload rather than reporting success over an empty DuckDB."""
    fake_credentials["pairs"] = [
        {"project": "bad-1", "region": "us"},
        {"project": "bad-2", "region": "eu"},
    ]
    db_path = tmp_path / "profiler_extract.db"
    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = fake_credentials

    def _always_fail(_sql_filename, _substitution_vars, _bq_client, project_region):
        raise RuntimeError(f"simulated failure for {project_region}")

    monkeypatch.setattr(bq_metadata_extract, "_run_sql_for_iteration", _always_fail)
    with pytest.raises(SystemExit) as exc_info:
        bq_metadata_extract.execute(
            credential_manager=cred_manager,
            bigquery_client_factory=lambda *_a, **_kw: MagicMock(),
            db_path=str(db_path),
        )
    assert exc_info.value.code == 1
    err_payload = json.loads([line for line in capsys.readouterr().err.strip().split("\n") if line][-1])
    assert err_payload["status"] == "error"
    assert "all (project, region) pairs" in err_payload["message"].lower()
