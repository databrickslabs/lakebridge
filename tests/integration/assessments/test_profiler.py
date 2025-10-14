from pathlib import Path

import shutil
import tempfile
import yaml
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler import Profiler


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


def test_profile_execution() -> None:
    """Test successful profiling execution using actual pipeline configuration"""
    profiler = Profiler("synapse")
    path_prefix = Path(__file__).parent / "../../../"
    config_file = path_prefix / "tests/resources/assessments/pipeline_config_main.yml"
    config = profiler.path_modifier(config_file=config_file, path_prefix=path_prefix)
    profiler.profile(pipeline_config=config)
    assert Path("/tmp/profiler_main/profiler_extract.db").exists(), "Profiler extract database should be created"


def test_profile_execution_with_invalid_config() -> None:
    """Test profiling execution with invalid configuration"""
    profiler = Profiler("synapse")
    path_prefix = Path(__file__).parent / "../../../"
    with pytest.raises(FileNotFoundError):
        config_file = path_prefix / "tests/resources/assessments/invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(
            config_file=config_file,
            path_prefix=path_prefix,
        )
        profiler.profile(pipeline_config=pipeline_config)


def test_profile_execution_config_override() -> None:
    """Test successful profiling execution using actual pipeline configuration with config file override"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Copy the YAML file and Python script to the temp directory
        prefix = Path(__file__).parent / ".." / ".."
        config_file_src = prefix / Path("resources/assessments/pipeline_config_absolute.yml")
        config_file_dest = Path(temp_dir) / config_file_src.name
        script_src = prefix / Path("resources/assessments/db_extract.py")
        script_dest = Path(temp_dir) / script_src.name
        shutil.copy(script_src, script_dest)

        with open(config_file_src, 'r', encoding="utf-8") as file:
            config_data = yaml.safe_load(file)
            for step in config_data['steps']:
                step['extract_source'] = str(script_dest)
        with open(config_file_dest, 'w', encoding="utf-8") as file:
            yaml.safe_dump(config_data, file)

        profiler = Profiler("synapse")
        pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
        profiler.profile(pipeline_config=pipeline_config)
        assert Path(
            "/tmp/profiler_absolute/profiler_extract.db"
        ).exists(), "Profiler extract database should be created"
