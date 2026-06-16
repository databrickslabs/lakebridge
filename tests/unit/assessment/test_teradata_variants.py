"""Unit tests for first-class Teradata variant entries in PROFILER_SOURCE_SYSTEM."""

import pytest

from databricks.labs.lakebridge.assessments import (
    CONNECTOR_REQUIRED,
    SOURCE_SYSTEM_TO_PIPELINE_CFG,
    PROFILER_SOURCE_SYSTEM,
    TERADATA_VARIANTS,
    source_system_family,
)

_TERADATA_PLATFORMS = [f"teradata_{variant}" for variant in TERADATA_VARIANTS]


@pytest.mark.parametrize("platform", _TERADATA_PLATFORMS)
def test_teradata_variants_listed_as_profiler_sources(platform: str) -> None:
    assert platform in PROFILER_SOURCE_SYSTEM


def test_teradata_variants_require_connector() -> None:
    assert CONNECTOR_REQUIRED["teradata"] is True


@pytest.mark.parametrize("platform", _TERADATA_PLATFORMS)
def test_teradata_variants_have_pipeline_config_path(platform: str) -> None:
    cfg_path = SOURCE_SYSTEM_TO_PIPELINE_CFG[platform]
    assert cfg_path is not None
    # Path should reference the correct variant folder
    variant = platform.removeprefix("teradata_")
    assert f"/teradata/{variant}/pipeline_config.yml" in cfg_path


@pytest.mark.parametrize("platform", _TERADATA_PLATFORMS)
def test_source_system_family_collapses_teradata_variants(platform: str) -> None:
    assert source_system_family(platform) == "teradata"


def test_source_system_family_passes_through_non_teradata_platforms() -> None:
    assert source_system_family("synapse") == "synapse"
    assert source_system_family("mssql") == "mssql"
