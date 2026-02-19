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
def test_make_alphanum_or_semver(value: str) -> None:
    value = make_alphanum_or_semver(value)
    assert alphanum_pattern.match(value) or semver_pattern.match(value)


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"LAKEBRIDGE_ENTRYPOINT": "cli"}, "cli"),
        ({"LAKEBRIDGE_ENTRYPOINT": "desktop-app"}, "desktop-app"),
        ({"LAKEBRIDGE_ENTRYPOINT": "databricks-app"}, "databricks-app"),
        ({"LAKEBRIDGE_ENTRYPOINT": "  CLI  "}, "cli"),
        ({"LAKEBRIDGE_ENTRYPOINT": "Desktop-App"}, "desktop-app"),
        ({"LAKEBRIDGE_ENTRYPOINT": "DATABRICKS-APP"}, "databricks-app"),
        ({}, "cli"),
        ({"LAKEBRIDGE_ENTRYPOINT": "invalid"}, "cli"),
    ],
)
def test_get_entrypoint_from_env(env: dict[str, str], expected: str) -> None:
    assert get_entrypoint_from_env(env) == expected


def test_get_entrypoint_uses_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAKEBRIDGE_ENTRYPOINT", "desktop-app")
    assert get_entrypoint_from_env() == "desktop-app"
