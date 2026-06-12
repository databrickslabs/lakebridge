"""Unit tests for first-class Redshift variant entries in PROFILER_SOURCE_SYSTEM."""

import inspect

import pytest

from databricks.labs.lakebridge.assessments import (
    CONNECTOR_REQUIRED,
    SOURCE_SYSTEM_TO_PIPELINE_CFG,
    PROFILER_SOURCE_SYSTEM,
    REDSHIFT_VARIANTS,
    source_system_family,
)
from databricks.labs.lakebridge.assessments.profiler import Profiler

_REDSHIFT_PLATFORMS = [f"redshift_{variant}" for variant in REDSHIFT_VARIANTS]


@pytest.mark.parametrize("platform", _REDSHIFT_PLATFORMS)
def test_redshift_variants_listed_as_profiler_sources(platform: str) -> None:
    assert platform in PROFILER_SOURCE_SYSTEM


def test_redshift_variants_require_connector() -> None:
    assert CONNECTOR_REQUIRED["redshift"] is True


@pytest.mark.parametrize("platform", _REDSHIFT_PLATFORMS)
def test_redshift_variants_have_pipeline_config_path(platform: str) -> None:
    cfg_path = SOURCE_SYSTEM_TO_PIPELINE_CFG[platform]
    assert cfg_path is not None
    # Path should reference the correct variant folder
    variant = platform.removeprefix("redshift_")
    assert f"/redshift/{variant}/pipeline_config.yml" in cfg_path


@pytest.mark.parametrize("platform", _REDSHIFT_PLATFORMS)
def test_source_system_family_collapses_redshift_variants(platform: str) -> None:
    assert source_system_family(platform) == "redshift"


def test_source_system_family_passes_through_non_redshift_platforms() -> None:
    assert source_system_family("synapse") == "synapse"
    assert source_system_family("mssql") == "mssql"


def test_profiler_create_signature_has_no_redshift_variant_kwarg() -> None:
    # The variant kwarg has been removed; create takes only a platform name.
    parameters = inspect.signature(Profiler.create).parameters
    assert "redshift_variant" not in parameters
    assert list(parameters) == ["platform"]
