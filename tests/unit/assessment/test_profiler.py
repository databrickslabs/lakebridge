from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.profiler import Profiler, get_pipeline

# Sources with a single root-level pipeline_config.yml (no variant subpaths).
UNIFIED_PROFILER_SOURCES = ("redshift", "teradata")


@pytest.mark.parametrize("source", UNIFIED_PROFILER_SOURCES)
def test_profile_missing_platform_config(source: str) -> None:
    with pytest.raises(ValueError, match=f"Cannot Proceed without a valid pipeline configuration for {source}"):
        Profiler(source).profile()


@pytest.mark.parametrize("source", UNIFIED_PROFILER_SOURCES)
def test_profile_execution_with_invalid_config(source: str, test_resources: Path) -> None:
    profiler = Profiler(source)
    with pytest.raises(FileNotFoundError):
        config_file = test_resources / "assessments" / "invalid_pipeline_config.yml"
        pipeline_config = profiler.path_modifier(config_file=config_file, path_prefix=test_resources)
        profiler.profile(pipeline_config=pipeline_config, output_folder=test_resources / "out")


@pytest.mark.parametrize("source", UNIFIED_PROFILER_SOURCES)
def test_unified_pipeline_resolves_without_variant(source: str) -> None:
    cfg_path = get_pipeline(source, None)
    assert str(cfg_path).endswith(f"/{source}/pipeline_config.yml")
