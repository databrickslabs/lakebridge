import json
import logging
from collections.abc import Sequence, Set, Mapping
from pathlib import Path
from typing import cast
from unittest.mock import create_autospec, PropertyMock, Mock

import pytest
import yaml

from databricks.labs.blueprint.installation import JsonList, JsonObject

from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPConfig
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


def _dump_lsp_config_option(option: LSPConfigOptionV1) -> JsonObject:
    """Convert an LSPConfigOptionV1 into its serialized dictionary form."""
    serialized: JsonObject = {
        # Mandatory fields.
        "flag": option.flag,
        "method": option.method.name,
    }
    # Optional fields, exclude if None.
    if option.prompt is not None:
        serialized["prompt"] = option.prompt
    if option.choices is not None:
        serialized["choices"] = cast(JsonList, option.choices)
    if option.default is not None:
        serialized["default"] = option.default
    return serialized


def transpiler_config(
    transpiler_name: str,
    *,
    version: int = 1,
    dialects: Sequence[str] = ("foo",),
    options: Mapping[str, Sequence[LSPConfigOptionV1]] | None = None,
) -> JsonObject:
    """Create a configuration dictionary for a transpiler."""
    config: JsonObject = {
        "remorph": {
            "version": version,
            "name": transpiler_name,
            "dialects": list(dialects),
            "command_line": ["a_command"],
        }
    }
    if options is not None:
        config["options"] = {
            dialect: [_dump_lsp_config_option(option) for option in options] for dialect, options in options.items()
        }
    return config


def guard_path() -> Path:
    """A mock path that doesn't exist and reports no children (instead of infinite mocks)."""
    guard_file = create_autospec(Path)
    type(guard_file).name = PropertyMock(return_value="<guard>")
    guard_file.exists.return_value = False
    guard_file.is_file.return_value = False
    guard_file.is_dir.return_value = False
    guard_file.iterdir.return_value = iter([])
    guard_file.__truediv__.return_value = guard_file
    guard_file.read_text.side_effect = FileNotFoundError("No such file or directory")
    return guard_file


def mock_transpiler(transpiler_id: str, config: JsonObject, version="1.0.0") -> Path:
    """Create a mock path representing a transpiler installation."""
    transpiler_root = create_autospec(Path)
    type(transpiler_root).name = PropertyMock(return_value=transpiler_id)
    transpiler_root.is_dir.return_value = True

    transpiler_lib = create_autospec(Path)
    type(transpiler_lib).name = PropertyMock(return_value="lib")
    transpiler_lib.is_dir.return_value = True

    config_yml = create_autospec(Path)
    type(config_yml).name = PropertyMock(return_value="config.yml")
    config_yml.is_file.return_value = True
    config_yml.read_text.return_value = yaml.dump(config)
    transpiler_lib.__truediv__.side_effect = lambda path: config_yml if path == "config.yml" else guard_path()

    transpiler_state = create_autospec(Path)
    type(transpiler_state).name = PropertyMock(return_value="state")
    transpiler_state.is_dir.return_value = True

    version_json = create_autospec(Path)
    type(version_json).name = PropertyMock(return_value="version.json")
    version_json.is_file.return_value = True
    version_json.read_text.return_value = json.dumps(
        {
            "version": f"v{version}",
            "date": "2025-09-23T16:03:25.123456Z",
        }
    )
    transpiler_state.__truediv__.side_effect = lambda path: version_json if path == "version.json" else guard_path()

    transpiler_root.__truediv__.side_effect = lambda path: {
        "lib": transpiler_lib,
        "state": transpiler_state,
    }.get(path, guard_path())

    return transpiler_root


def mock_labs_path_with_registry(transpiler_paths: Sequence[Path]) -> Mock:
    """Create a mock path representing the labs directory containing a transpiler registry."""
    labs_dir = create_autospec(Path)
    registry_dir = create_autospec(Path)
    # labs_dir / "remorph-transpiler" resolves to remorph_mock
    labs_dir.__truediv__.side_effect = lambda path: registry_dir if path == "remorph-transpilers" else guard_path()

    registry_dir.iterdir.side_effect = lambda: iter(transpiler_paths)
    registry_dir.__truediv__.side_effect = lambda path: {
        transpiler.name: transpiler for transpiler in transpiler_paths
    }.get(path, guard_path())

    return labs_dir


def test_all_transpiler_configurations() -> None:
    """Verify that the repository yields the set of transpiler configurations that are available."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo",)))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar",)))
    another_b = mock_transpiler("another_b", transpiler_config("B Transpiler", dialects=("baz",)))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler, another_b))
    transpiler_repository = TranspilerRepository(labs_dir)

    transpiler_configurations = transpiler_repository.all_transpiler_configs()

    # Note: keyed by product name, not transpiler name.
    assert transpiler_configurations == {
        "a_transpiler": LSPConfig.load(a_transpiler / "lib" / "config.yml"),
        "b_transpiler": LSPConfig.load(b_transpiler / "lib" / "config.yml"),
        "another_b": LSPConfig.load(another_b / "lib" / "config.yml"),
    }


def test_no_transpiler_configuration() -> None:
    """Verify that an empty repository yields no transpiler configurations."""
    labs_dir = mock_labs_path_with_registry(transpiler_paths=())

    transpiler_repository = TranspilerRepository(labs_dir)

    transpiler_configurations = transpiler_repository.all_transpiler_configs()
    assert transpiler_configurations == {}


def test_os_error_listing_transpilers(caplog) -> None:
    """Verify the handling of an OS-based error when listing transpilers: treat as empty."""
    labs_dir = mock_labs_path_with_registry(transpiler_paths=())
    # Simulate a permissions error when trying to enumerate the transpilers.
    repository_dir = labs_dir / "remorph-transpilers"
    repository_dir.iterdir.side_effect = PermissionError("Simulated permission error")
    transpiler_repository = TranspilerRepository(labs_dir)

    with caplog.at_level(logging.DEBUG, logger=transpiler_repository.__module__):
        transpiler_configurations = transpiler_repository.all_transpiler_configs()

    assert transpiler_configurations == {}
    debug_messages = [record.message for record in caplog.records if record.levelno == logging.DEBUG]
    assert "Unable to list installed transpilers" in debug_messages


def test_all_transpiler_names() -> None:
    """Verify that the transpiler names (not product) for installed transpilers is available."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo",)))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar",)))
    another_b = mock_transpiler("another_b", transpiler_config("B Transpiler", dialects=("baz",)))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler, another_b))
    transpiler_repository = TranspilerRepository(labs_dir)

    transpiler_names = transpiler_repository.all_transpiler_names()

    assert transpiler_names == {"A Transpiler", "B Transpiler"}


def test_all_dialects() -> None:
    """Verify that the dialects for installed transpilers can be queried. No duplicates."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo", "bar")))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar", "baz")))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler))
    transpiler_repository = TranspilerRepository(labs_dir)

    dialects = transpiler_repository.all_dialects()

    assert dialects == {"foo", "bar", "baz"}


@pytest.mark.parametrize(
    ("dialect", "expected_transpiler_names"),
    (
        ("foo", {"A Transpiler"}),
        ("bar", {"A Transpiler", "B Transpiler"}),
        ("baz", {"B Transpiler"}),
    ),
)
def test_transpilers_for_dialect(dialect: str, expected_transpiler_names: Set[str]) -> None:
    """Verify that the transpiler names for a given dialect can be queried."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo", "bar")))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar", "baz")))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler))
    transpiler_repository = TranspilerRepository(labs_dir)

    transpiler_names = transpiler_repository.transpilers_with_dialect(dialect)

    assert transpiler_names == expected_transpiler_names


def test_transpiler_config_path() -> None:
    """Verify that the path for a given transpiler configuration can be queried."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo",)))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar",)))
    c_transpiler = mock_transpiler("c_transpiler", transpiler_config("C Transpiler", dialects=("baz",)))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler, c_transpiler))
    transpiler_repository = TranspilerRepository(labs_dir)

    config_path = transpiler_repository.transpiler_config_path(transpiler_name="B Transpiler")
    assert config_path == b_transpiler / "lib" / "config.yml"


def test_transpiler_config_path_missing() -> None:
    """Verify an error is raised if requesting the configuration path for an unknown transpiler."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo",)))
    labs_dir = mock_labs_path_with_registry((a_transpiler,))
    transpiler_repository = TranspilerRepository(labs_dir)

    with pytest.raises(ValueError, match=r"^No such transpiler: Unknown Transpiler$"):
        transpiler_repository.transpiler_config_path(transpiler_name="Unknown Transpiler")


def test_transpiler_config_path_duplicates() -> None:
    """Verify handling of the corner case where multiple installed transpilers have the same name."""
    a_transpiler = mock_transpiler("a_transpiler", transpiler_config("A Transpiler", dialects=("foo",)))
    b_transpiler = mock_transpiler("b_transpiler", transpiler_config("B Transpiler", dialects=("bar",)))
    another_b = mock_transpiler("another_b", transpiler_config("B Transpiler", dialects=("baz",)))
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler, another_b))
    transpiler_repository = TranspilerRepository(labs_dir)

    config_path = transpiler_repository.transpiler_config_path("B Transpiler")

    # First it finds is the one it will use.
    assert config_path == b_transpiler / "lib" / "config.yml"


def test_transpiler_options() -> None:
    """Verify that a transpilers options for a given dialect can be queried."""
    a_transpiler = mock_transpiler(
        "a_transpiler",
        transpiler_config(
            "A Transpiler",
            dialects=("foo", "bar"),
            # These options for "bar" should NOT be returned.
            options={"bar": (LSPConfigOptionV1(flag="b-flag", method=LSPPromptMethod.CONFIRM, prompt="A question?"),)},
        ),
    )
    b_bar_options = [
        LSPConfigOptionV1(
            flag="a-flag",
            method=LSPPromptMethod.CHOICE,
            prompt="Are you superstitious?",
            choices=["Very", "No, just a little"],
        ),
    ]
    b_transpiler = mock_transpiler(
        "b_transpiler",
        transpiler_config(
            "B Transpiler",
            dialects=("bar",),
            # These options for "bar" should be returned.
            options={"bar": b_bar_options},
        ),
    )
    labs_dir = mock_labs_path_with_registry((a_transpiler, b_transpiler))
    transpiler_repository = TranspilerRepository(labs_dir)

    options = transpiler_repository.transpiler_config_options(transpiler_name="B Transpiler", source_dialect="bar")

    assert options == b_bar_options


def test_get_installed_transpiler_version() -> None:
    """Verify that the version of an installed transpiler can be queried."""
    a_transpiler = mock_transpiler(
        transpiler_id="a_transpiler",
        config=transpiler_config("A Transpiler"),
        version="1.2.3",
    )
    labs_dir = mock_labs_path_with_registry((a_transpiler,))
    transpiler_repository = TranspilerRepository(labs_dir)

    version = transpiler_repository.get_installed_version(transpiler_id="a_transpiler")
    assert version == "1.2.3"


def test_get_missing_transpiler_version() -> None:
    """Verify handling if we request the version of a transpiler that is not installed."""
    labs_dir = mock_labs_path_with_registry(transpiler_paths=())
    transpiler_repository = TranspilerRepository(labs_dir)

    version = transpiler_repository.get_installed_version("missing_transpiler")
    assert version is None
