from importlib import resources
from pathlib import Path

import pytest

import databricks.labs.lakebridge.resources.assessments as assessment_resources
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.assessments.profiler import Profiler

TERADATA_PLATFORMS = ["teradata_core", "teradata_pdcr"]


@pytest.mark.parametrize("platform", TERADATA_PLATFORMS)
def test_teradata_profile_missing_platform_config(platform: str) -> None:
    with pytest.raises(ValueError, match=f"Cannot Proceed without a valid pipeline configuration for {platform}"):
        Profiler(platform).profile()


@pytest.mark.parametrize("platform", TERADATA_PLATFORMS)
def test_teradata_profile_execution_with_invalid_config(platform: str, test_resources: Path) -> None:
    """Test Teradata profiling execution with invalid configuration."""
    profiler = Profiler(platform)
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config, output_folder=test_resources / "out")


def _load_variant_config(variant: str) -> PipelineConfig:
    root = resources.files(assessment_resources)
    config_def = root.joinpath("teradata").joinpath(variant).joinpath("pipeline_config.yml")
    with resources.as_file(config_def) as config_path:
        return PipelineClass.load_config_from_yaml(Path(config_path))


def test_teradata_core_variant_extracts_dbql_core_and_no_pdcr() -> None:
    config = _load_variant_config("core")
    step_names = {step.name for step in config.steps if step.flag == "active"}
    assert "td_dbql_core_info_extract" in step_names
    assert "td_pdcr_info_agg_extract" not in step_names
    assert "td_pdcr_sp_exe_info_agg_extract" not in step_names


def test_teradata_pdcr_variant_extracts_pdcr_and_no_dbql_core() -> None:
    config = _load_variant_config("pdcr")
    step_names = {step.name for step in config.steps if step.flag == "active"}
    assert "td_pdcr_info_agg_extract" in step_names
    assert "td_pdcr_sp_exe_info_agg_extract" in step_names
    assert "td_dbql_core_info_extract" not in step_names
