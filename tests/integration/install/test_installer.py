from pathlib import Path

import pytest

from databricks.labs.lakebridge.install import MavenInstaller, WheelInstaller, WorkspaceInstaller

# TODO: These should run as part of the integration tests, not a separate test suite.


def test_gets_maven_artifact_version() -> None:
    version = MavenInstaller.get_current_maven_artifact_version("com.databricks", "databricks-connect")
    assert version is not None
    check_valid_version(version)


def test_downloads_from_maven(tmp_path: Path) -> None:
    pom_path = tmp_path / "pom.xml"
    success = MavenInstaller.download_artifact_from_maven(
        "com.databricks", "databricks-connect", "16.0.0", pom_path, extension="pom"
    )
    assert success
    assert pom_path.exists()
    assert pom_path.stat().st_size == 5_684


def test_gets_pypi_artifact_version() -> None:
    version = WheelInstaller.get_latest_artifact_version_from_pypi("databricks-labs-remorph")
    assert version is not None
    check_valid_version(version)


def check_valid_version(version: str) -> None:
    parts = version.split(".")
    for _, part in enumerate(parts):
        try:
            _ = int(part)
        except ValueError:
            assert False, f"{version} does not look like a valid semver"


def test_java_version() -> None:
    result = WorkspaceInstaller.find_java()
    match result:
        case None:
            # Fine, no Java available.
            pass
        case (java_home, tuple(version)):
            assert java_home.exists() and version >= (11, 0, 0, 0)
        case _:
            pytest.fail(f"Unexpected result from WorkspaceInstaller.find_java(): {result!r}")
