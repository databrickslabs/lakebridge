import pytest

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS
from databricks.labs.lakebridge.assessments.profiler import get_pipeline

TERADATA_VARIANTS = SOURCE_SYSTEM_VARIANTS["teradata"]


@pytest.mark.parametrize("variant", TERADATA_VARIANTS)
def test_teradata_variants_have_pipeline_config_path(variant: str) -> None:
    cfg_path = get_pipeline("teradata", variant)
    assert f"/teradata/{variant}/pipeline_config.yml" in str(cfg_path)
