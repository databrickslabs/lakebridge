from importlib import resources
from pathlib import Path

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig


def _load_pipeline_config() -> PipelineConfig:
    root = resources.files(assessment_resources)
    config_def = root.joinpath("teradata").joinpath("pipeline_config.yml")
    with resources.as_file(config_def) as config_path:
        return PipelineClass.load_config_from_yaml(Path(config_path))


def test_teradata_unified_pipeline_includes_core_and_pdcr_metrics() -> None:
    config = _load_pipeline_config()
    step_names = {step.name for step in config.steps if step.flag == "active"}
    assert "td_dbql_core_info_extract" in step_names
    assert "td_pdcr_info_agg_extract" in step_names
    assert "td_pdcr_sp_exe_info_agg_extract" in step_names


def test_teradata_pdcr_sql_steps_are_optional() -> None:
    config = _load_pipeline_config()
    steps_by_name = {step.name: step for step in config.steps if step.flag == "active"}

    dbql_sql = steps_by_name["td_dbql_core_info_extract"]
    assert dbql_sql.type == "sql"
    assert dbql_sql.optional is False

    for pdcr_step in ("td_pdcr_info_agg_extract", "td_pdcr_sp_exe_info_agg_extract"):
        sql_step = steps_by_name[pdcr_step]
        assert sql_step.type == "sql"
        assert sql_step.optional is True


def test_teradata_pdcr_ddl_steps_are_required() -> None:
    config = _load_pipeline_config()
    ddl_steps = [step for step in config.steps if step.flag == "active" and step.type == "ddl"]
    pdcr_ddl = [step for step in ddl_steps if step.name.startswith("td_pdcr")]
    assert len(pdcr_ddl) == 2
    assert all(not step.optional for step in pdcr_ddl)
