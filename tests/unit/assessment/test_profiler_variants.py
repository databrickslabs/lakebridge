import pytest

from databricks.labs.lakebridge.assessments import AUTO, SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import get_pipeline

# Explicit tuple variants only — AUTO sources are probed at runtime (see test_mssql_variants).
SOURCE_VARIANT_PAIRS = [
    (source, variant) for source, variants in SOURCE_SYSTEM_VARIANTS.items() for variant in variants if variant != AUTO
]


@pytest.mark.parametrize("source,variant", SOURCE_VARIANT_PAIRS)
def test_variants_resolve_pipeline_config_path(source: str, variant: str) -> None:
    cfg_path = get_pipeline(source, variant)
    assert f"/{source}/{variant}/pipeline_config.yml" in str(cfg_path)


def test_unified_sources_have_no_fixed_variant_choices() -> None:
    """Redshift and Teradata use a single pipeline config without variant subpaths."""
    assert "redshift" not in SOURCE_SYSTEM_VARIANTS
    assert "teradata" not in SOURCE_SYSTEM_VARIANTS
