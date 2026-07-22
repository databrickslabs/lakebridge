from importlib import resources
from pathlib import Path

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def _load_pipeline_config() -> PipelineConfig:
    root = resources.files(assessment_resources)
    config_def = root.joinpath("redshift").joinpath("pipeline_config.yml")
    with resources.as_file(config_def) as config_path:
        return PipelineClass.load_config_from_yaml(Path(config_path))


def test_redshift_unified_pipeline_includes_universal_and_optional_metrics() -> None:
    config = _load_pipeline_config()
    step_names = {step.name for step in config.steps if step.flag == "active"}
    assert "query_view" in step_names
    assert "rs_spectrum_tb_month" in step_names
    assert "rs_managed_storage_gb" in step_names
    assert "rs_nodes" in step_names
    assert "chart_cpu_consumption_by_query_type" in step_names
    assert "cost_incurred" in step_names


def test_redshift_deployment_bound_sql_steps_are_optional() -> None:
    config = _load_pipeline_config()
    optional_sql = [step for step in config.steps if step.flag == "active" and step.type == "sql" and step.optional]
    optional_by_name: dict[str, list[Step]] = {}
    for step in optional_sql:
        optional_by_name.setdefault(step.name, []).append(step)

    assert len(optional_by_name["rs_managed_storage_gb"]) == 2
    assert len(optional_by_name["rs_nodes"]) == 2
    assert len(optional_by_name["cost_incurred"]) == 1

    stv_storage = next(s for s in optional_by_name["rs_managed_storage_gb"] if s.extract_source.endswith("_stv.sql"))
    serverless_storage = next(
        s for s in optional_by_name["rs_managed_storage_gb"] if s.extract_source.endswith("_serverless.sql")
    )
    assert stv_storage.optional is True
    assert serverless_storage.optional is True


def test_redshift_universal_sql_steps_are_required() -> None:
    config = _load_pipeline_config()
    required_names = {
        "rs_spectrum_tb_month",
        "rs_avg_concurrent_users",
        "rs_avg_queries_minute",
        "chart_query_type_by_hour",
        "chart_cpu_consumption_by_query_type",
        "chart_concurrent_users_by_hour",
        "chart_cpu_consumption_by_hour_and_query_type",
    }
    for step in config.steps:
        if step.flag != "active" or step.type != "sql":
            continue
        if step.name in required_names:
            assert step.optional is False, step.name


def _read_step_sql(extract_source: str) -> str:
    sql_name = Path(extract_source).name
    root = resources.files(assessment_resources)
    sql_resource = root.joinpath("redshift").joinpath("sql").joinpath(sql_name)
    with resources.as_file(sql_resource) as sql_path:
        return sql_path.read_text(encoding="utf-8")


def test_redshift_cpu_charts_use_sys_query_detail() -> None:
    config = _load_pipeline_config()
    cpu_steps = [
        step for step in config.steps if step.flag == "active" and step.name.startswith("chart_cpu_consumption")
    ]
    assert len(cpu_steps) == 2
    for step in cpu_steps:
        sql = _read_step_sql(step.extract_source)
        assert "sys_query_detail" in sql
        assert "stl_query_metrics" not in sql


def test_redshift_query_type_by_hour_uses_query_view() -> None:
    config = _load_pipeline_config()
    step = next(s for s in config.steps if s.name == "chart_query_type_by_hour" and s.flag == "active")
    sql = _read_step_sql(step.extract_source)
    assert "from query_view" in sql.lower()
    assert "sys_query_history" not in sql.lower()
