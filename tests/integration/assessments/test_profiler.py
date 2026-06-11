from pathlib import Path

import shutil
import yaml
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, make_profiler_db_filename
from databricks.labs.lakebridge.assessments.profiler import Profiler


def test_profile_missing_platform_config() -> None:
    """Constructing Profiler directly with no pipeline_config and no override raises."""
    with pytest.raises(ValueError, match="Cannot Proceed without a valid pipeline configuration for synapse"):
        profiler = Profiler("synapse")
        profiler.profile()


def test_profile_execution(test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution using actual pipeline configuration"""
    config_file = test_resources / "assessments" / "pipeline_config_main.yml"
    output_folder = tmp_path / "profiler_main"
    profiler = Profiler("synapse")
    config = Profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
    profiler.profile(pipeline_config=config, output_folder=output_folder)
    assert (
        output_folder / make_profiler_db_filename("synapse")
    ).exists(), "Profiler extract database should be created"


def test_profile_execution_with_invalid_config(test_resources: Path, tmp_path: Path) -> None:
    """Test profiling execution with invalid configuration"""
    profiler = Profiler("synapse")
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = Profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config, output_folder=tmp_path / "out")


def test_profile_execution_config_override(test_resources: Path, tmp_path: Path) -> None:
    """Test successful profiling execution using actual pipeline configuration with config file override"""
    config_dir = tmp_path / "config_dir"
    config_dir.mkdir()
    output_folder = tmp_path / "profiler_absolute"
    # Copy the YAML file and Python script to the temp directory
    config_file_src = test_resources / "assessments" / "pipeline_config_absolute.yml"
    config_file_dest = config_dir / config_file_src.name
    script_src = test_resources / "assessments" / "db_extract.py"
    script_dest = config_dir / script_src.name
    shutil.copy(script_src, script_dest)

    with open(config_file_src, 'r', encoding="utf-8") as file:
        config_data = yaml.safe_load(file)
    for step in config_data['steps']:
        step['extract_source'] = str(script_dest)
    with open(config_file_dest, 'w', encoding="utf-8") as file:
        yaml.safe_dump(config_data, file)

    profiler = Profiler("synapse")
    pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
    profiler.profile(pipeline_config=pipeline_config, output_folder=output_folder)
    assert (
        output_folder / make_profiler_db_filename("synapse")
    ).exists(), "Profiler extract database should be created"
