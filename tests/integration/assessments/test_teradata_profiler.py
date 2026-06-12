from pathlib import Path

from unittest.mock import MagicMock

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, make_profiler_db_filename
from databricks.labs.lakebridge.assessments.profiler import Profiler
from tests.unit.profiler_test_helpers import build_overridden_pipeline_config


def test_teradata_profile_execution(test_resources: Path, tmp_path: Path) -> None:
    """Test successful Teradata profiling execution with pipeline config."""
    profiler = Profiler("teradata", True)
    config_file = test_resources / "assessments" / "pipeline_config_main.yml"
    output_folder = tmp_path / "teradata_profiler_main"
    config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
    profiler.profile(pipeline_config=config, extractor=MagicMock(), output_folder=output_folder)
    assert (
        output_folder / make_profiler_db_filename("teradata")
    ).exists(), "Profiler extract database should be created"


def test_teradata_profile_execution_config_override(test_resources: Path, tmp_path: Path) -> None:
    """Test Teradata profiling execution with overridden config file."""
    config_file_dest, _extract_folder = build_overridden_pipeline_config(
        test_resources=test_resources,
        tmp_path=tmp_path,
        config_dir_name="config_dir_teradata",
        extract_dir_name="teradata_profiler_absolute",
    )

    output_folder = tmp_path / "teradata_profiler_db"
    profiler = Profiler("teradata", True)
    pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
    profiler.profile(pipeline_config=pipeline_config, extractor=MagicMock(), output_folder=output_folder)
    assert (
        output_folder / make_profiler_db_filename("teradata")
    ).exists(), "Profiler extract database should be created"
