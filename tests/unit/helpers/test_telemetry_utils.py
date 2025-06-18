import pytest
from databricks.sdk.useragent import alphanum_pattern, semver_pattern

from databricks.labs.lakebridge.helpers.telemetry_utils import make_alphanum_or_semver


@pytest.mark.parametrize(
    "value",
    [
        "alpha",
        "0alpha",
        "12alpha",
        "alpha0",
        "alpha12",
        "0",
        "a b",
        "a-b",
        "a.b",
        "a+b",
        "a*b",
        "@&x2",
    ],
)
def test_make_alphanum_or_semver(value: str):
    value = make_alphanum_or_semver(value)
    assert alphanum_pattern.match(value) or semver_pattern.match(value)
