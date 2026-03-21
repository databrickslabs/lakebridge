from pathlib import Path

import shutil
import yaml
import pytest
from unittest.mock import MagicMock

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.assessments.profiler import Profiler


def test_teradata_as_supported_source_technologies() -> None:
    profiler = Profiler("teradata", None)
    supported_platforms = profiler.supported_platforms()
    assert isinstance(supported_platforms, list)
    assert "teradata" in supported_platforms


def test_teradata_profile_missing_platform_config() -> None:
    with pytest.raises(ValueError, match="Cannot Proceed without a valid pipeline configuration for teradata"):
        profiler = Profiler("teradata", None)
        profiler.profile()


def test_teradata_profile_execution(test_resources: Path, tmp_path: Path) -> None:
    """Test successful Teradata profiling execution with pipeline config."""
    profiler = Profiler("teradata")
    config_file = test_resources / "assessments" / "pipeline_config_main.yml"
    extract_folder = tmp_path / "teradata_profiler_main"
    config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources).copy(
        extract_folder=str(extract_folder)
    )
    profiler.profile(pipeline_config=config, extractor=MagicMock())
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"


def test_teradata_profile_execution_with_invalid_config(test_resources: Path) -> None:
    """Test Teradata profiling execution with invalid configuration."""
    profiler = Profiler("teradata")
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config, extractor=MagicMock())


def test_teradata_profile_execution_config_override(test_resources: Path, tmp_path: Path) -> None:
    """Test Teradata profiling execution with overridden config file."""
    config_dir = tmp_path / "config_dir_teradata"
    config_dir.mkdir()
    extract_folder = tmp_path / "teradata_profiler_absolute"
    config_file_src = test_resources / "assessments" / "pipeline_config_absolute.yml"
    config_file_dest = config_dir / config_file_src.name
    script_src = test_resources / "assessments" / "db_extract.py"
    script_dest = config_dir / script_src.name
    shutil.copy(script_src, script_dest)

    with open(config_file_src, 'r', encoding="utf-8") as file:
        config_data = yaml.safe_load(file)
    config_data['extract_folder'] = str(extract_folder)
    for step in config_data['steps']:
        step['extract_source'] = str(script_dest)
    with open(config_file_dest, 'w', encoding="utf-8") as file:
        yaml.safe_dump(config_data, file)

    profiler = Profiler("teradata")
    pipeline_config = PipelineClass.load_config_from_yaml(config_file_dest)
    profiler.profile(pipeline_config=pipeline_config, extractor=MagicMock())
    assert (extract_folder / "profiler_extract.db").exists(), "Profiler extract database should be created"


def test_configure_teradata_pipeline_disables_pdcr_steps() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        extract_folder="~/.databricks/labs/lakebridge_profilers/teradata_assessment",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="ddl", extract_source="a_ddl.sql", flag="active"),
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="ddl", extract_source="b_ddl.sql", flag="active"),
            Step(name="td_pdcr_sp_exe_info_agg_extract", type="sql", extract_source="b.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="ddl", extract_source="c_ddl.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    updated = Profiler._configure_teradata_pipeline(config, {"profiler": {"use_pdcr": False}})
    steps_by_key = {(step.name, step.type): step for step in updated.steps}

    assert steps_by_key[("td_pdcr_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_key[("td_pdcr_sp_exe_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_sp_exe_info_agg_extract", "sql")].flag == "inactive"
    assert steps_by_key[("td_dbql_core_info_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_dbql_core_info_extract", "sql")].flag == "active"


def test_configure_teradata_pipeline_keeps_pdcr_when_enabled() -> None:
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        extract_folder="/tmp/teradata_assessment",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="ddl", extract_source="a_ddl.sql", flag="active"),
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    updated = Profiler._configure_teradata_pipeline(config, {"profiler": {"use_pdcr": True}})
    steps_by_key = {(step.name, step.type): step for step in updated.steps}

    assert steps_by_key[("td_pdcr_info_agg_extract", "ddl")].flag == "active"
    assert steps_by_key[("td_pdcr_info_agg_extract", "sql")].flag == "active"
    assert steps_by_key[("td_dbql_core_info_extract", "sql")].flag == "inactive"


def test_configure_teradata_pipeline_defaults_to_pdcr() -> None:
    """When profiler config is missing or doesn't specify use_pdcr, default to PDCR enabled."""
    config = PipelineConfig(
        name="teradata_assessment",
        version="1.0",
        extract_folder="/tmp/teradata_assessment",
        steps=[
            Step(name="td_pdcr_info_agg_extract", type="sql", extract_source="a.sql", flag="active"),
            Step(name="td_dbql_core_info_extract", type="sql", extract_source="c.sql", flag="inactive"),
        ],
    )

    # No profiler key at all
    updated = Profiler._configure_teradata_pipeline(config, {})
    assert updated.steps[0].flag == "active"
    assert updated.steps[1].flag == "inactive"

    # Empty profiler config
    updated = Profiler._configure_teradata_pipeline(config, {"profiler": {}})
    assert updated.steps[0].flag == "active"
    assert updated.steps[1].flag == "inactive"
