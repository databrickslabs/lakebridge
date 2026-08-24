from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENT_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"
_PENDING_DDL_STEPS = {
    ("legacy_synapse", "columns"),
    ("legacy_synapse", "databases"),
    ("legacy_synapse", "requests"),
    ("legacy_synapse", "routines"),
    ("legacy_synapse", "sessions"),
    ("legacy_synapse", "storage_info"),
    ("legacy_synapse", "tables"),
    ("legacy_synapse", "views"),
    ("redshift", "chart_concurrent_users_by_hour"),
    ("redshift", "chart_cpu_consumption_by_hour_and_query_type"),
    ("redshift", "chart_cpu_consumption_by_query_type"),
    ("redshift", "chart_query_type_by_hour"),
    ("redshift", "cost_incurred"),
    ("redshift", "rs_avg_concurrent_users"),
    ("redshift", "rs_avg_queries_minute"),
    ("redshift", "rs_managed_storage_gb"),
    ("redshift", "rs_nodes"),
    ("redshift", "rs_spectrum_tb_month"),
    ("snowflake", "account_info"),
    ("snowflake", "automatic_clustering"),
    ("snowflake", "database_objects"),
    ("snowflake", "materialized_view_refresh"),
    ("snowflake", "pipe_usage"),
    ("snowflake", "query_history"),
    ("snowflake", "query_samples"),
    ("snowflake", "rate_sheet"),
    ("snowflake", "storage_usage"),
    ("snowflake", "warehouse_usage"),
}


def test_every_production_sql_step_references_existing_ddl() -> None:
    """Reject new omissions while the three source-specific DDL follow-ups are stacked."""
    invalid_steps: list[str] = []

    for config_path in sorted(_ASSESSMENT_RESOURCES.glob("**/pipeline_config.yml")):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        for step in config["steps"]:
            if step["type"] != "sql":
                continue
            ddl_source = step.get("ddl_source")
            if not ddl_source:
                if (config_path.parent.name, step["name"]) in _PENDING_DDL_STEPS:
                    continue
                invalid_steps.append(f"{config_path.relative_to(_REPO_ROOT)}: {step['name']} has no ddl_source")
                continue
            if not (_REPO_ROOT / ddl_source).is_file():
                invalid_steps.append(
                    f"{config_path.relative_to(_REPO_ROOT)}: {step['name']} references missing {ddl_source}"
                )

    assert not invalid_steps, "\n" + "\n".join(invalid_steps)
