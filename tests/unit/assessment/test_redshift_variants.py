import pytest

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import get_pipeline

REDSHIFT_VARIANTS = SOURCE_SYSTEM_VARIANTS["redshift"]


@pytest.mark.parametrize("variant", REDSHIFT_VARIANTS)
def test_redshift_variants_have_pipeline_config_path(variant: str) -> None:
    cfg_path = get_pipeline("redshift", variant)
    assert f"/redshift/{variant}/pipeline_config.yml" in str(cfg_path)
