"""Unit tests for the BigQuery pricing analysis step.

Pre-populates a DuckDB file with the minimum upstream tables `bq_pricing_analysis.py`
needs (`timeline_analysis`, `bq_cluster_pricing`, `bq_sqlwarehouse_pricing`,
`consumption_beyond_commitments`, `consumption_through_commitments`), runs the script,
and asserts the 4 derived entities land in the DuckDB with the expected shape.

Also asserts `TUNING_INPUT_PARAMS` keys haven't drifted from the upstream dict.
"""

import json
import sys
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest

from databricks.labs.lakebridge.resources.assessments.bigquery import bq_pricing_analysis
from databricks.labs.lakebridge.resources.assessments.bigquery.common.tuning_params import TUNING_INPUT_PARAMS


def _seed_input_tables(db_path: str) -> None:
    """Write the minimum upstream tables bq_pricing_analysis.py reads from."""
    timeline = pd.DataFrame(
        {
            "time_window": ["2026-04-01T10", "2026-04-01T11", "2026-05-15T09"],
            "metadata_level": ["proj-a.region-us"] * 3,
            "workload_type": ["BI", "ETL", "ETL"],
            "slot_secs": [120.0, 240.0, 360.0],
            "slots_avg": [10.0, 20.0, 30.0],
            "slots_perc_50th": [8.0, 18.0, 28.0],
            "slots_perc_90th": [12.0, 22.0, 32.0],
            "slots_perc_99th": [14.0, 24.0, 34.0],
            "slots_max": [15.0, 25.0, 35.0],
            "cumulative_secs_spent_in_exec": [600.0, 1200.0, 1800.0],
            "source": ["seed"] * 3,
        }
    )
    cluster_pricing = pd.DataFrame(
        {
            "cloud": ["gcp", "aws", "azure"],
            "instance_name": ["n2-highmem-8", "i3.2xlarge", "E8ds_v4"],
            "vcpu": [8, 8, 8],
            "mem": [64.0, 61.0, 64.0],
            "ssd_tb": [0.0, 1.9, 0.0],
            "jobs_dbu": [0.6, 0.71, 0.61],
            "jobs_photon_dbu": [1.5, 1.775, 1.525],
            "all_purp_photon_dbu": [1.2, 1.42, 1.22],
            "vm_per_hr": [0.40, 0.62, 0.50],
        }
    )
    sqlwarehouse_pricing = pd.DataFrame(
        {
            "cloud": ["GCP", "AWS", "AZURE"],
            "sku": ["serverless"] * 3,
            "cluster_size": ["Medium"] * 3,
            "worker_node_count": [4, 4, 4],
            "worker_cpu_count": [56, 56, 56],
            "worker_node_instance": ["n2-highmem-8"] * 3,
            "driver_node_instance": ["n2-highmem-8"] * 3,
            "dbu_per_hr": [12, 13, 13],
        }
    )
    consumption_beyond = pd.DataFrame(
        {
            "metadata_level": ["proj-a.region-us"],
            "edition": ["STANDARD"],
            "total_slot_seconds": [1000.0],
        }
    )
    consumption_through = pd.DataFrame(
        {
            "metadata_level": ["proj-a.region-us"],
            "edition": ["ENTERPRISE"],
            "commitment_plan": ["MONTHLY"],
            "total_slot_seconds": [500.0],
        }
    )

    with duckdb.connect(db_path) as conn:
        for name, df in (
            ("timeline_analysis", timeline),
            ("bq_cluster_pricing", cluster_pricing),
            ("bq_sqlwarehouse_pricing", sqlwarehouse_pricing),
            ("consumption_beyond_commitments", consumption_beyond),
            ("consumption_through_commitments", consumption_through),
        ):
            conn.register("seed_df", df)
            conn.execute(f"CREATE TABLE {name} AS SELECT * FROM seed_df")
            conn.unregister("seed_df")


def _run_pricing_execute(monkeypatch, tmp_path, target_cloud: str = "gcp"):
    db_path = tmp_path / "profiler_extract.db"
    _seed_input_tables(str(db_path))

    creds_file = tmp_path / "credentials.yml"
    creds_file.write_text("placeholder: true\n")

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = {"target_cloud": target_cloud}
    monkeypatch.setattr(bq_pricing_analysis, "create_credential_manager", lambda *_a, **_kw: cred_manager)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bq_pricing_analysis.py",
            "--db-path",
            str(db_path),
            "--credential-config-path",
            str(creds_file),
        ],
    )

    bq_pricing_analysis.execute()
    return db_path


def test_pricing_analysis_produces_4_derived_entities(monkeypatch, tmp_path, capsys):
    db_path = _run_pricing_execute(monkeypatch, tmp_path)
    with duckdb.connect(str(db_path), read_only=True) as conn:
        # Tables (not views) — input_params + 2 CTAS outputs
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "input_params" in tables
        assert "bq_slots_pricing_analysis" in tables
        assert "monthly_weighted_pricing" in tables
        # consumption_by_commitment is a VIEW
        view_count = conn.execute(
            "SELECT COUNT(*) FROM information_schema.views "
            "WHERE table_schema = 'main' AND table_name = 'consumption_by_commitment'"
        ).fetchone()[0]
        assert view_count == 1

        # Substantive checks: each derived entity should have rows for our seeded data.
        assert conn.execute("SELECT COUNT(*) FROM bq_slots_pricing_analysis").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM monthly_weighted_pricing").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM consumption_by_commitment").fetchone()[0] == 2
        # input_params is a single-row table.
        assert conn.execute("SELECT COUNT(*) FROM input_params").fetchone()[0] == 1
        assert conn.execute("SELECT target_cloud FROM input_params").fetchone()[0] == "gcp"

    # Final stdout line is the structured success payload.
    captured = capsys.readouterr()
    last_line = [line for line in captured.out.strip().split("\n") if line][-1]
    payload = json.loads(last_line)
    assert payload["status"] == "success"
    assert set(payload["tables"]) == {
        "input_params",
        "bq_slots_pricing_analysis",
        "monthly_weighted_pricing",
        "consumption_by_commitment",
    }
    assert "wall_clock_seconds" in payload


def test_pricing_analysis_rejects_unknown_target_cloud(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "profiler_extract.db"
    _seed_input_tables(str(db_path))
    creds_file = tmp_path / "credentials.yml"
    creds_file.write_text("placeholder: true\n")

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = {"target_cloud": "ibm-cloud"}
    monkeypatch.setattr(bq_pricing_analysis, "create_credential_manager", lambda *_a, **_kw: cred_manager)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bq_pricing_analysis.py",
            "--db-path",
            str(db_path),
            "--credential-config-path",
            str(creds_file),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        bq_pricing_analysis.execute()
    assert exc_info.value.code == 1
    err_text = capsys.readouterr().err
    assert "Unknown target_cloud" in err_text


def test_exclude_pricing_analysis_skips_step_2(monkeypatch, tmp_path, capsys):
    """Setting `profiler.exclude_pricing_analysis: true` should make step 2 exit cleanly
    without touching DuckDB. Customers who only want raw extracts opt out via this flag."""
    db_path = tmp_path / "profiler_extract.db"
    _seed_input_tables(str(db_path))
    creds_file = tmp_path / "credentials.yml"
    creds_file.write_text("placeholder: true\n")

    cred_manager = MagicMock()
    cred_manager.get_credentials.return_value = {
        "target_cloud": "gcp",
        "profiler": {"exclude_pricing_analysis": True},
    }
    monkeypatch.setattr(bq_pricing_analysis, "create_credential_manager", lambda *_a, **_kw: cred_manager)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bq_pricing_analysis.py",
            "--db-path",
            str(db_path),
            "--credential-config-path",
            str(creds_file),
        ],
    )

    bq_pricing_analysis.execute()

    # DuckDB should NOT have any of the step-2 output tables / view.
    with duckdb.connect(str(db_path), read_only=True) as conn:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    for not_created in ("input_params", "bq_slots_pricing_analysis", "monthly_weighted_pricing"):
        assert not_created not in tables, f"{not_created} should not have been created"

    # Success payload signals the skip explicitly.
    captured = capsys.readouterr()
    last_line = [line for line in captured.out.strip().split("\n") if line][-1]
    payload = json.loads(last_line)
    assert payload["status"] == "skipped"
    assert "exclude_pricing_analysis" in payload["message"]


def test_tuning_params_has_three_clouds_with_required_keys():
    """Regression guard: the upstream tuning_input_params dict has 3 clouds and a fixed
    set of keys that the bq_slots_pricing_analysis SQL substitution expects. If anyone
    edits tuning_params.py, this fails loudly rather than silently breaking the format()
    call at runtime."""
    assert set(TUNING_INPUT_PARAMS) == {"aws", "azure", "gcp"}
    required_keys = {
        "db_cores_to_bq_slots_ratio",
        "db_etl_performance_factor",
        "db_sql_performance_factor",
        "db_etl_drivers_per_cluster",
        "db_etl_executors_per_cluster",
        "db_etl_cores_per_executor",
        "db_sku",
        "db_sql_cluster_size",
        "etl_instance_type",
        "db_dbsql_pricing",
        "db_jobs_photon_pricing",
        "db_etl_effective_price_perf",
        "db_sql_effective_price_perf",
        "bq_slot_pricing",
    }
    for cloud, params in TUNING_INPUT_PARAMS.items():
        missing = required_keys - set(params)
        assert not missing, f"{cloud} tuning params missing keys: {missing}"
