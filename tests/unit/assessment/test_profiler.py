from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def test_configure_teradata_pipeline_disables_pdcr_steps() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        extract_folder="/tmp/teradata",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="sql", extract_source="b.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    updated = Profiler._configure_teradata_pipeline(config, {"profiler": {"use_pdcr": False}})
    steps_by_name = {step.name: step for step in updated.steps}

    assert steps_by_name["td_pdcr_info_agg_extract"].flag == "inactive"
    assert steps_by_name["td_pdcr_sp_exe_info_agg_extract"].flag == "inactive"
    assert steps_by_name["td_dbql_core_info_extract"].flag == "active"
