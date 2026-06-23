"""Unit tests for the public BigQuery profiler dashboard and its extract validation schema.

The dashboard is intentionally BigQuery-source-only: all Databricks-side pricing / TCO /
complexity is excluded (it is internal IP). These tests guard that contract and keep the
validation schema aligned with the extract's analysis types.
"""

import json
from importlib import resources
from pathlib import Path

import yaml

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.deployment.dashboard import ProfilerDashboardTemplateLoader

# The 12 source tables produced by bq_metadata_extract (see bigquery/resources/analysis_types.json).
EXPECTED_EXTRACT_TABLES = {
    "fulfillment_analysis",
    "table_storage",
    "timeline_analysis",
    "workload_types",
    "consumption_beyond_commitments",
    "consumption_through_commitments",
    "commitment_changes",
    "commitments",
    "jobs_timeline_by_reservations",
    "reservation_timeline_analysis",
    "streaming_summary",
    "write_api_summary",
}

# Tokens that would indicate Databricks-side pricing/TCO leaked into the public dashboard.
FORBIDDEN_TCO_TOKENS = (
    "db_price",
    "db_savings",
    "bq_slots_pricing_analysis",
    "input_params",
    "bq_cluster_pricing",
    "target_cloud",
    "slot_estimation",
)


def _dashboards_dir() -> Path:
    return Path(str(resources.files(assessment_resources).joinpath("dashboards/bigquery")))


def test_bigquery_dashboard_template_loads() -> None:
    dashboard = ProfilerDashboardTemplateLoader(_dashboards_dir()).load("bigquery")
    assert dashboard["datasets"], "dashboard should define datasets"
    assert dashboard["pages"], "dashboard should define pages"


def test_bigquery_dashboard_has_no_tco_content() -> None:
    dashboard = ProfilerDashboardTemplateLoader(_dashboards_dir()).load("bigquery")
    serialized = json.dumps(dashboard).lower()
    assert "<catalog_name>" in serialized and "<schema_name>" in serialized
    leaked = [token for token in FORBIDDEN_TCO_TOKENS if token in serialized]
    assert not leaked, f"public BigQuery dashboard must not contain TCO content: {leaked}"


def test_bigquery_extract_schema_tables() -> None:
    schema_path = Path(str(resources.files(assessment_resources).joinpath("validation/bigquery_extract_schema.yml")))
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    assert schema["source_tech"] == "bigquery"
    assert set(schema["schemas"]["main"]["tables"]) == EXPECTED_EXTRACT_TABLES
