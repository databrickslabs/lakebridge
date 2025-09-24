import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest

from databricks.labs.lakebridge.transpiler.installers import ArtifactInstaller, MorpheusInstaller


def test_store_product_state(tmp_path) -> None:
    """Verify the product state is stored after installing is correct."""

    class MockArtifactInstaller(ArtifactInstaller):
        @classmethod
        def store_product_state(cls, product_path: Path, version: str) -> None:
            cls._store_product_state(product_path, version)

    # Store the product state, capturing the time before and after so we can verify the timestamp it puts in there.
    before = dt.datetime.now(tz=dt.timezone.utc)
    MockArtifactInstaller.store_product_state(tmp_path, "1.2.3")
    after = dt.datetime.now(tz=dt.timezone.utc)

    # Load the state that was just stored.
    with (tmp_path / "state" / "version.json").open("r", encoding="utf-8") as f:
        stored_state = json.load(f)

    # Verify the timestamp first.
    stored_date = stored_state["date"]
    parsed_date = dt.datetime.fromisoformat(stored_date)
    assert parsed_date.tzinfo is not None, "Stored date should be timezone-aware."
    assert before <= parsed_date <= after, f"Stored date {stored_date} is not within the expected range."

    # Verify the rest, now that we've checked the timestamp.
    expected_state = {
        "version": "v1.2.3",
        "date": stored_date,
    }
    assert stored_state == expected_state


@pytest.fixture
def no_java(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure that (temporarily) no 'java' binary can be found in the environment."""
    found_java = shutil.which("java")
    while found_java is not None:
        # Java is installed, so we need to figure out how to remove it from the path.
        # (We loop here to handle cases where multiple java binaries are available via the PATH.)
        java_directory = Path(found_java).parent
        search_path = os.environ.get("PATH", os.defpath).split(os.pathsep)
        updated_path = os.pathsep.join(p for p in search_path if p and Path(p) != java_directory)
        assert (
            search_path != updated_path
        ), f"Did not find {java_directory} in {search_path}, but 'java' was found at {found_java}."

        # Set the modified PATH without the directory where 'java' was found.
        monkeypatch.setenv("PATH", os.pathsep.join(updated_path))

        # Check again if 'java' is still found.
        found_java = shutil.which("java")


def test_java_version_with_java_missing(no_java: None) -> None:
    """Verify the Java version check handles Java missing entirely."""
    expected_missing = MorpheusInstaller.find_java()
    assert expected_missing is None


class FriendOfMorpheusInstaller(MorpheusInstaller):
    """A friend class to access protected methods for testing purposes."""

    @classmethod
    def parse_java_version(cls, output: str) -> tuple[int, int, int, int] | None:
        return cls._parse_java_version(output)


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        # Real examples.
        pytest.param("1.8.0_452", None, id="1.8.0_452"),
        pytest.param("11.0.27", (11, 0, 27, 0), id="11.0.27"),
        pytest.param("17.0.15", (17, 0, 15, 0), id="17.0.15"),
        pytest.param("21.0.7", (21, 0, 7, 0), id="21.0.7"),
        pytest.param("24.0.1", (24, 0, 1, 0), id="24.0.1"),
        # All digits.
        pytest.param("1.2.3.4", (1, 2, 3, 4), id="1.2.3.4"),
        # Trailing zeros can be omitted.
        pytest.param("1.2.3", (1, 2, 3, 0), id="1.2.3"),
        pytest.param("1.2", (1, 2, 0, 0), id="1.2"),
        pytest.param("1", (1, 0, 0, 0), id="1"),
        # Another edge case.
        pytest.param("", None, id="empty string"),
    ),
)
def test_java_version_parse(version: str, expected: tuple[int, int, int, int] | None) -> None:
    """Verify that the Java version parsing works correctly."""
    # Format reference: https://docs.oracle.com/en/java/javase/11/install/version-string-format.html
    version_output = f'openjdk version "{version}" 2025-06-19'
    parsed = FriendOfMorpheusInstaller.parse_java_version(version_output)
    assert parsed == expected


def test_java_version_parse_missing() -> None:
    """Verify that we return None when the version is missing."""
    version_output = "Nothing in here that looks like a version."
    parsed = FriendOfMorpheusInstaller.parse_java_version(version_output)
    assert parsed is None
