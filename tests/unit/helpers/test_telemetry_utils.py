import pytest
from databricks.sdk.useragent import alphanum_pattern, semver_pattern

from databricks.labs.lakebridge.helpers.telemetry_utils import make_alphanum_or_semver, get_entrypoint_from_env


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


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("desktop-app", "desktop-app"),  # Verify it uses os.environ
        (None, "cli"),  # Verify default when key not present
        ("invalid", "cli"),  # Verify default when value is invalid
    ],
)
def test_get_entrypoint_uses_os_environ_by_default(env_value, expected, monkeypatch):
    if env_value is None:
        monkeypatch.delenv("LAKEBRIDGE_ENTRYPOINT", raising=False)
    else:
        monkeypatch.setenv("LAKEBRIDGE_ENTRYPOINT", env_value)
    result = get_entrypoint_from_env()
    assert result == expected
