from pathlib import Path
from unittest.mock import create_autospec

import pytest

from databricks.labs.blueprint.installation import RootJsonValue

from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.transpiler.describe import TranspilersDescription
from databricks.labs.lakebridge.transpiler.repository import TranspilerInfo, TranspilerRepository


@pytest.mark.parametrize(
    ("option", "expected_json"),
    (
        (
            LSPConfigOptionV1("foo", LSPPromptMethod.FORCE, default="boo"),
            {"flag": "foo", "method": "FORCE", "default": "boo"},
        ),
        (
            LSPConfigOptionV1("bar", LSPPromptMethod.QUESTION, prompt="Would you?"),
            {"flag": "bar", "method": "QUESTION", "prompt": "Would you?"},
        ),
        (
            LSPConfigOptionV1("baz", LSPPromptMethod.QUESTION, prompt="Would you?", default="yup"),
            {"flag": "baz", "method": "QUESTION", "prompt": "Would you?", "default": "yup"},
        ),
        (
            LSPConfigOptionV1("gaz", LSPPromptMethod.CHOICE, prompt="Pick one", choices=["a", "b", "c"]),
            {"flag": "gaz", "method": "CHOICE", "prompt": "Pick one", "choices": ["a", "b", "c"]},
        ),
        (
            LSPConfigOptionV1("haz", LSPPromptMethod.CHOICE, prompt="Pick ONE", choices=["A", "B", "C"], default="A"),
            {"flag": "haz", "method": "CHOICE", "prompt": "Pick ONE", "choices": ["A", "B", "C"], "default": "A"},
        ),
        (
            LSPConfigOptionV1("fiz", LSPPromptMethod.CONFIRM, prompt="Do you take...?"),
            {"flag": "fiz", "method": "CONFIRM", "prompt": "Do you take...?"},
        ),
    ),
)
def test_dialect_options_as_json(option: LSPConfigOptionV1, expected_json: RootJsonValue) -> None:
    """Verify how dialect options are represented as JSON."""
    as_json = TranspilersDescription.dialect_options_as_json(option)
    assert as_json == expected_json


def test_transpiler_info_as_json() -> None:
    """Verify how transpiler information is represented as JSON."""
    transpiler_info = TranspilerInfo(
        transpiler_name="test",
        version="1.0.0",
        configuration_path=Path("/path/to/config.yml"),
        dialects={
            "foo": [],
            "bar": [LSPConfigOptionV1("an_option", LSPPromptMethod.FORCE, default="its_value")],
        },
    )
    expected_json = {
        "name": "test",
        "config-path": "/path/to/config.yml",
        "versions": {
            "installed": "1.0.0",
            "latest": None,
        },
        "supported-dialects": {
            "foo": {"options": []},
            "bar": {"options": [{"flag": "an_option", "method": "FORCE", "default": "its_value"}]},
        },
    }
    result = TranspilersDescription.transpiler_as_json(transpiler_info)
    assert result == expected_json


def test_transpiler_info_missing_version_as_json() -> None:
    """Verify how transpiler information is represented as JSON when its version information is missing."""
    transpiler_info = TranspilerInfo(
        transpiler_name="test", version=None, configuration_path=Path("/path/to/config.yml"), dialects={"foo": []}
    )
    expected_json = {
        "name": "test",
        "config-path": "/path/to/config.yml",
        "versions": {
            "installed": None,
            "latest": None,
        },
        "supported-dialects": {"foo": {"options": []}},
    }
    result = TranspilersDescription.transpiler_as_json(transpiler_info)
    assert result == expected_json


def test_transpiler_repository_as_json() -> None:
    """Verify the complete description of installed transpilers as JSON."""
    mock_repository = create_autospec(TranspilerRepository)
    mock_repository.all_dialects.return_value = {"foo", "bar"}
    mock_repository.installed_transpilers.return_value = {
        "foo": TranspilerInfo(
            transpiler_name="Foo",
            version="1.0.0",
            configuration_path=Path("/path/to/foo/config.yml"),
            dialects={"foo": []},
        ),
        "bar": TranspilerInfo(
            transpiler_name="Bar",
            version="0.1.0",
            configuration_path=Path("/path/to/bar/config.yml"),
            dialects={
                "bar": [LSPConfigOptionV1("an_option", LSPPromptMethod.CONFIRM, prompt="Have you any wool?")],
            },
        ),
    }

    result = TranspilersDescription(mock_repository).as_json()
    assert mock_repository.all_dialects.call_count == 1
    assert mock_repository.installed_transpilers.call_count == 1
    assert result == {
        # Note: sorted alphabetically
        "available-dialects": ["bar", "foo"],
        "installed-transpilers": [
            {
                "name": "Bar",
                "config-path": "/path/to/bar/config.yml",
                "versions": {
                    "installed": "0.1.0",
                    "latest": None,
                },
                "supported-dialects": {
                    "bar": {"options": [{"flag": "an_option", "method": "CONFIRM", "prompt": "Have you any wool?"}]},
                },
            },
            {
                "name": "Foo",
                "config-path": "/path/to/foo/config.yml",
                "versions": {
                    "installed": "1.0.0",
                    "latest": None,
                },
                "supported-dialects": {
                    "foo": {"options": []},
                },
            },
        ],
    }
