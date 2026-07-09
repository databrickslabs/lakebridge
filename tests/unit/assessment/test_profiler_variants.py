import pytest

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import get_pipeline

# Every (source, variant) pair declared in the registry. Empty today after Redshift
# and Teradata were consolidated into unified pipelines; kept so future variant-based
# sources are covered without per-source duplication.
SOURCE_VARIANT_PAIRS = [
    (source, variant) for source, variants in SOURCE_SYSTEM_VARIANTS.items() for variant in variants
]


@pytest.mark.parametrize("source,variant", SOURCE_VARIANT_PAIRS)
def test_variants_resolve_pipeline_config_path(source: str, variant: str) -> None:
    cfg_path = get_pipeline(source, variant)
    assert f"/{source}/{variant}/pipeline_config.yml" in str(cfg_path)


def test_no_source_system_variants_registered() -> None:
    """Profiler sources currently resolve without a variant subpath."""
    assert SOURCE_SYSTEM_VARIANTS == {}
