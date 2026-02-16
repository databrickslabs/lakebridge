from pathlib import Path

import shutil
import yaml
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler import Profiler

# Config file names for script-based execution tests (no live DB)
PLATFORM_MAIN_CONFIG = {"synapse": "pipeline_config_main.yml", "redshift": "pipeline_config_main_redshift.yml"}
PLATFORM_EXTRACT_SCRIPT = {"synapse": "db_extract.py", "redshift": "db_extract_redshift.py"}


@pytest.mark.parametrize("platform", ["synapse", "redshift"])
def test_supported_source_technologies(platform: str) -> None:
    """Test that supported source technologies are correctly returned"""
    profiler = Profiler(platform, None)
    supported_platforms = profiler.supported_platforms()
    assert isinstance(supported_platforms, list)
    assert platform in supported_platforms


@pytest.mark.parametrize("platform", ["synapse", "redshift"])
def test_profile_missing_platform_config(platform: str) -> None:
    """Test that profiling without config raises ValueError"""
    with pytest.raises(ValueError, match=f"Cannot Proceed without a valid pipeline configuration for {platform}"):
        profiler = Profiler(platform, None)
        profiler.profile()


@pytest.mark.parametrize("platform", ["synapse", "redshift"])
def test_profile_execution(platform: str, test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution using script-based pipeline (no live DB)"""
    profiler = Profiler(platform)
    config_file = test_resources / "assessments" / PLATFORM_MAIN_CONFIG[platform]
    extract_folder = tmp_path / "profiler_main"
    config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources).copy(
        extract_folder=str(extract_folder)
    )
    profiler.profile(pipeline_config=config)
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"


@pytest.mark.parametrize("platform", ["synapse", "redshift"])
def test_profile_execution_with_invalid_config(platform: str, test_resources: Path) -> None:
    """Test profiling execution with invalid configuration"""
    profiler = Profiler(platform)
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config)


@pytest.mark.parametrize("platform", ["synapse", "redshift"])
def test_profile_execution_config_override(platform: str, test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution with config file override (script-based, no live DB)"""
    config_dir = tmp_path / "config_dir"
    config_dir.mkdir()
    extract_folder = tmp_path / "profiler_absolute"
    config_file_src = test_resources / "assessments" / "pipeline_config_absolute.yml"
    config_file_dest = config_dir / config_file_src.name
    script_src = test_resources / "assessments" / PLATFORM_EXTRACT_SCRIPT[platform]
    script_dest = config_dir / script_src.name
    shutil.copy(script_src, script_dest)

    with open(config_file_src, "r", encoding="utf-8") as file:
        config_data = yaml.safe_load(file)
    config_data["extract_folder"] = str(extract_folder)
    for step in config_data["steps"]:
        step["extract_source"] = str(script_dest)
    with open(config_file_dest, "w", encoding="utf-8") as file:
        yaml.safe_dump(config_data, file)

    profiler = Profiler(platform)
    pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
    profiler.profile(pipeline_config=pipeline_config)
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"
