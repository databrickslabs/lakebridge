"""Integration test for the BigQuery profiler step.

Runs the `bq_metadata_extract.py` step against a real BigQuery project supplied via env vars.
Marked `xfail` when CI vault entries are unset — the test still attempts to run, but failures
are tolerated. Activation in upstream CI depends on the TEST_BQ_* env vars being seeded into
the Databricks-Labs CI vault (tracked separately as a follow-up issue).
"""

import os
import sys

import duckdb
import pytest

from databricks.labs.lakebridge.resources.assessments.bigquery import bq_metadata_extract

_REQUIRED_VARS = ("TEST_BQ_PROJECT_ID", "TEST_BQ_REGION")
_MISSING = [v for v in _REQUIRED_VARS if not os.getenv(v)]

pytestmark = pytest.mark.xfail(
    bool(_MISSING),
    reason=f"BQ integration env vars missing: {_MISSING}. Pending CI vault provisioning.",
    strict=False,
)


def test_bigquery_extract_runs_end_to_end(tmp_path, sandbox_bigquery_cred_config) -> None:
    """Run bq_metadata_extract against the configured BQ project; assert tables land in DuckDB.

    Intentionally light-touch: we verify the script's contract (DuckDB written, success JSON
    printed, expected analysis_type tables exist) rather than asserting specific row counts —
    BQ data shape changes over time and tight asserts would brittle this test. Per-SQL contract
    tests are a v2 item (Part 7 #7 of the design).
    """
    db_path = tmp_path / "profiler_extract.db"

    # Skip the credential-manager indirection — feed the config directly.
    class _DirectCredManager:
        def get_credentials(self, _source: str) -> dict:
            return sandbox_bigquery_cred_config["bigquery"]

    original_create = bq_metadata_extract.create_credential_manager
    bq_metadata_extract.create_credential_manager = lambda *_a, **_kw: _DirectCredManager()  # type: ignore[assignment]

    original_argv = sys.argv
    creds_file = tmp_path / "credentials.yml"
    creds_file.write_text("placeholder: true\n")
    sys.argv = [
        "bq_metadata_extract.py",
        "--db-path",
        str(db_path),
        "--credential-config-path",
        str(creds_file),
    ]

    try:
        bq_metadata_extract.execute()
    finally:
        sys.argv = original_argv
        bq_metadata_extract.create_credential_manager = original_create  # type: ignore[assignment]

    with duckdb.connect(str(db_path)) as conn:
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

    # 12 analysis types + 2 reference tables expected. With exclude_reservations_data=True
    # in the fixture, 6 reservation/commitment tables are skipped — adjust accordingly.
    expected_minimum = {
        "fulfillment_analysis",
        "table_storage",
        "timeline_analysis",
        "workload_types",
        "streaming_summary",
        "write_api_summary",
        "bq_cluster_pricing",
        "bq_sqlwarehouse_pricing",
    }
    missing = expected_minimum - tables
    assert not missing, f"DuckDB extract missing expected tables: {missing}. Present: {tables}"
