from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def _sql_step(name: str, flag: str = "active", **kwargs) -> Step:
    return Step(
        name=name,
        type="sql",
        extract_source="dummy.sql",
        ddl_source="dummy_ddl.sql",
        flag=flag,
        **kwargs,
    )


def test_pipeline_config_accepts_sql_with_ddl_source() -> None:
    config = PipelineConfig(
        name="test",
        version="1.0",
        steps=[_sql_step("inventory"), Step(name="py1", type="python", extract_source="x.py")],
    )
    assert len(config.steps) == 2


def test_pipeline_config_accepts_source_ddl_without_ddl_source() -> None:
    config = PipelineConfig(
        name="test",
        version="1.0",
        steps=[Step(name="view", type="source_ddl", extract_source="view.sql"), _sql_step("metric")],
    )
    assert config.steps[0].ddl_source is None
