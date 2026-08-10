import json
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest
import yaml

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.resources.assessments.bigquery import bq_metadata_extract
from databricks.labs.lakebridge.resources.assessments.common.sql_substituter import substitute

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BQ_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/bigquery"
_BIGQUERY_VARIANTS = ("inventory_and_ddl", "inventory")


def _fake_run_sql_for_iteration(sql_filename, _substitution_vars, _bq_client, project_region):
    df = pd.DataFrame(
        {"metadata_level": [project_region], "active_logical_tb": [1.5], "source": f"{project_region}_{sql_filename}"}
    )
    return df, 0.01


@pytest.fixture
def fake_credentials():
    return {
        "pairs": [{"project": "proj-a", "region": "us"}],
        "profiler": {
            "profiling_window_days": 180,
            "max_parallel_sqls": 2,
            "redact_query_text": True,
            "exclude_reservations_data": False,
            "exclude_streaming_metrics": False,
        },
    }


def _run_execute(monkeypatch, tmp_path, credentials, *, sql_file_map, success_message="ok"):
    db_path = tmp_path / "profiler_extract.db"

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = credentials
    monkeypatch.setattr(bq_metadata_extract, "_run_sql_for_iteration", _fake_run_sql_for_iteration)

    bq_metadata_extract.execute(
        credential_manager=cred_manager,
        bigquery_client_factory=lambda *_a, **_kw: MagicMock(),
        db_path=str(db_path),
        sql_file_map=sql_file_map,
        success_message=success_message,
    )
    return db_path


def _tables(db_path: Path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SHOW TABLES").fetchall()
    return {row[0] for row in rows}


def _row_count(db_path, table: str) -> int:
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])


def test_bigquery_variants_are_registered() -> None:
    assert SOURCE_SYSTEM_VARIANTS["bigquery"] == _BIGQUERY_VARIANTS


@pytest.mark.parametrize("variant", _BIGQUERY_VARIANTS)
def test_bigquery_variant_config_references_existing_files(variant: str) -> None:
    config = yaml.safe_load((_BQ_RESOURCES / variant / "pipeline_config.yml").read_text(encoding="utf-8"))
    for step in config["steps"]:
        extract_source = _REPO_ROOT / step["extract_source"]
        assert extract_source.exists(), f"{variant} step '{step['name']}' references missing file {extract_source}"


def test_inventory_and_ddl_pipeline_has_definitions_step() -> None:
    config = yaml.safe_load((_BQ_RESOURCES / "inventory_and_ddl" / "pipeline_config.yml").read_text(encoding="utf-8"))
    step_names = [step["name"] for step in config["steps"]]
    assert step_names == ["bq_inventory_extract", "bq_definitions_extract"]


def test_inventory_pipeline_omits_definitions_step() -> None:
    config = yaml.safe_load((_BQ_RESOURCES / "inventory" / "pipeline_config.yml").read_text(encoding="utf-8"))
    step_names = [step["name"] for step in config["steps"]]
    assert step_names == ["bq_inventory_extract"]


def test_exclude_streaming_metrics_yields_empty_streaming_tables(monkeypatch, tmp_path, fake_credentials):
    """Excluded SQLs don't run, but their tables still exist with empty stub schemas so
    downstream dashboard queries see them instead of erroring with table-not-found."""
    fake_credentials["profiler"]["exclude_streaming_metrics"] = True
    db_path = _run_execute(
        monkeypatch,
        tmp_path,
        fake_credentials,
        sql_file_map=bq_metadata_extract.INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
    )
    tables = _tables(db_path)
    assert "streaming_summary" in tables
    assert "write_api_summary" in tables
    assert _row_count(db_path, "streaming_summary") == 0
    assert _row_count(db_path, "write_api_summary") == 0
    assert "workload_types" in tables
    assert _row_count(db_path, "workload_types") > 0


def test_exclude_reservations_data_yields_empty_reservation_tables(monkeypatch, tmp_path, fake_credentials):
    """Same stub-schema behavior for reservation/commitment tables — empty rows when
    excluded, full schema preserved so downstream consumers see consistent tables."""
    fake_credentials["profiler"]["exclude_reservations_data"] = True
    db_path = _run_execute(
        monkeypatch,
        tmp_path,
        fake_credentials,
        sql_file_map=bq_metadata_extract.INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
    )
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
        sql_file_map=bq_metadata_extract.INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
        success_message="ok",
    )

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
            sql_file_map=bq_metadata_extract.INVENTORY_SQL_FILE_TO_ANALYSIS_TYPE,
            success_message="ok",
        )
    assert exc_info.value.code == 1
    err_payload = json.loads([line for line in capsys.readouterr().err.strip().split("\n") if line][-1])
    assert err_payload["status"] == "error"
    assert "all (project, region) pairs" in err_payload["message"].lower()
