from pathlib import Path

from unittest.mock import MagicMock

import pytest

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments import teradata_pipeline
import databricks.labs.lakebridge.assessments.profiler as profiler_module


def test_teradata_as_supported_source_technologies() -> None:
    profiler = Profiler("teradata", True)
    supported_platforms = profiler.supported_platforms()
    assert isinstance(supported_platforms, list)
    assert "teradata" in supported_platforms


def test_teradata_profile_missing_platform_config() -> None:
    with pytest.raises(ValueError, match="Cannot Proceed without a valid pipeline configuration for teradata"):
        profiler = Profiler("teradata", True)
        profiler.profile()


def test_teradata_profile_execution_with_invalid_config(test_resources: Path) -> None:
    """Test Teradata profiling execution with invalid configuration."""
    profiler = Profiler("teradata", True)
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config, extractor=MagicMock())


def testconfigure_teradata_pipeline_disables_pdcr_steps() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="ddl", extract_source="a_ddl.sql", flag="active"),
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="ddl", extract_source="b_ddl.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="sql", extract_source="b.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="ddl", extract_source="c_ddl.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    # use_pdcr=False short-circuits the probe (extractor unused) and toggles steps directly.
    updated = teradata_pipeline.configure_pipeline(config, {"profiler": {"use_pdcr": False}}, None)
    steps_by_key = {(step.name, step.type): step for step in updated.steps}

    assert steps_by_key[("td_pdcr_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_key[("td_pdcr_sp_exe_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_sp_exe_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_key[("td_dbql_core_info_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_dbql_core_info_extract", "sql")].flag == "active"


def testconfigure_teradata_pipeline_keeps_pdcr_when_enabled() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="ddl", extract_source="a_ddl.sql", flag="active"),
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    # use_pdcr=True and PDCR reachable (probe succeeds) -> pipeline kept as-is.
    extractor = MagicMock()
    updated = teradata_pipeline.configure_pipeline(config, {"profiler": {"use_pdcr": True}}, extractor)
    steps_by_key = {(step.name, step.type): step for step in updated.steps}

    assert steps_by_key[("td_pdcr_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_info_agg_extract", "sql")].flag == "active"
    assert steps_by_key[("td_dbql_core_info_extract", "sql")].flag == "inactive"


def testconfigure_teradata_pipeline_defaults_to_pdcr() -> None:
    """When profiler config is missing or doesn't specify use_pdcr, default to PDCR enabled."""
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    # PDCR reachable (probe succeeds) -> pipeline kept as default (PDCR) in both cases.
    extractor = MagicMock()

    # No profiler key at all
    updated = teradata_pipeline.configure_pipeline(config, {}, extractor)
    assert updated.steps[0].flag == "active"
    assert updated.steps[1].flag == "inactive"

    # Empty profiler config
    updated = teradata_pipeline.configure_pipeline(config, {"profiler": {}}, extractor)
    assert updated.steps[0].flag == "active"
    assert updated.steps[1].flag == "inactive"


def test_pdcr_probe_success_keeps_pipeline() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )
    extractor = MagicMock()  # fetch succeeds -> PDCR reachable

    updated = teradata_pipeline.configure_pipeline(config, {"profiler": {"use_pdcr": True}}, extractor)

    # Both PDCR preflight probes ran, and the PDCR pipeline was kept as-is.
    assert extractor.fetch.call_count == 2
    assert updated.steps[0].flag == "active"
    assert updated.steps[1].flag == "inactive"


def test_pdcr_probe_failure_falls_back_to_dbql_core() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )
    extractor = MagicMock()
    extractor.fetch.side_effect = ConnectionError("relation missing")

    updated = teradata_pipeline.configure_pipeline(config, {"profiler": {"use_pdcr": True}}, extractor)

    assert updated.steps[0].flag == "inactive"
    assert updated.steps[1].flag == "active"


def test_execute_teradata_auto_fallbacks_when_pdcr_unavailable(monkeypatch) -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="sql", extract_source="b.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    fake_cred_manager = MagicMock()
    fake_cred_manager.get_credentials.return_value = {"profiler": {"use_pdcr": True}}
    fake_extractor = MagicMock()
    captured: dict[str, PipelineConfig] = {}

    class _FakePipeline:
        def __init__(self, pipeline_config, executor, *_args):
            captured["config"] = pipeline_config
            self._executor = executor

        def execute(self):
            return []

    monkeypatch.setattr(profiler_module, "create_credential_manager", lambda *args, **kwargs: fake_cred_manager)
    monkeypatch.setattr(profiler_module, "DatabaseManager", lambda *args, **kwargs: fake_extractor)
    monkeypatch.setattr(teradata_pipeline, "_has_pdcr_access", lambda *args, **kwargs: False)
    monkeypatch.setattr(profiler_module, "PipelineClass", _FakePipeline)

    Profiler("teradata", True).profile(pipeline_config=config)

    steps_by_name = {(step.name, step.type): step for step in captured["config"].steps}
    assert steps_by_name[("td_pdcr_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_name[("td_pdcr_sp_exe_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_name[("td_dbql_core_info_extract", "sql")].flag == "active"
