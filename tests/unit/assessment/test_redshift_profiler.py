from importlib import resources
from pathlib import Path

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig


def _load_pipeline_config() -> PipelineConfig:
    root = resources.files(assessment_resources)
    config_def = root.joinpath("redshift").joinpath("pipeline_config.yml")
    with resources.as_file(config_def) as config_path:
        return PipelineClass.load_config_from_yaml(Path(config_path))


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
