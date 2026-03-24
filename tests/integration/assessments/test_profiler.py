from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler import Profiler
from tests.unit.profiler_test_helpers import build_overridden_pipeline_config


def test_supported_source_technologies() -> None:
    """Test that supported source technologies are correctly returned"""
    profiler = Profiler("synapse", None)
    supported_platforms = profiler.supported_platforms()
    assert isinstance(supported_platforms, list)
    assert "synapse" in supported_platforms


def test_profile_missing_platform_config() -> None:
    """Test that profiling an unsupported platform raises ValueError"""
    with pytest.raises(ValueError, match="Cannot Proceed without a valid pipeline configuration for synapse"):
        profiler = Profiler("synapse", None)
        profiler.profile()


def test_profile_execution(test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution using actual pipeline configuration"""
    profiler = Profiler("synapse")
    config_file = test_resources / "assessments" / "pipeline_config_main.yml"
    extract_folder = tmp_path / "profiler_main"
    config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources).copy(
        extract_folder=str(extract_folder)
    )
    profiler.profile(pipeline_config=config)
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"


def test_profile_execution_with_invalid_config(test_resources: Path) -> None:
    """Test profiling execution with invalid configuration"""
    profiler = Profiler("synapse")
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config)


def test_profile_execution_config_override(test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution using actual pipeline configuration with config file override"""
    config_file_dest, extract_folder = build_overridden_pipeline_config(
        test_resources=test_resources,
        tmp_path=tmp_path,
        config_dir_name="config_dir",
        extract_dir_name="profiler_absolute",
    )

    profiler = Profiler("synapse")
    pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
    profiler.profile(pipeline_config=pipeline_config)
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"
