from pathlib import Path
from typing import Any
from unittest.mock import patch

import copy
import pytest
import yaml

from databricks.labs.blueprint.installation import JsonValue
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from tests.unit.conftest import path_to_resource


def test_valid_config():
    config = path_to_resource("lsp_transpiler", "lsp_config.yml")
    engine = LSPEngine.from_config_path(Path(config))
    assert engine.supported_dialects == ["snowflake"]


VALID_CONFIG: dict[str, Any] = yaml.safe_load(
    """remorph:
  version: 1
  name: test-transpiler
  dialects:
    - snowflake
    - oracle
  environment:
    SOME_ENV: abc
  command_line:
    - python
    - lsp_server.py
custom:
  whatever: xyz
"""
)


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("version", None, r"Missing 'version' attribute"),
        ("version", 0, r"Unsupported transpiler config version"),
        ("name", None, r"Missing 'name' attribute"),
        ("name", 42, r"Invalid 'name' entry in \{.*\}, expecting a string"),
        ("name", "", r"Invalid 'name' attribute, must be a non-empty string"),
        ("dialects", None, r"Missing 'dialects' attribute"),
        ("dialects", [], r"Invalid 'dialects' attribute, expected a non-empty list of strings but got: \[\]"),
        ("command_line", None, r"Missing 'command_line' attribute"),
        ("command_line", [], r"Invalid 'command_line' attribute, expected a non-empty list of strings but got: \[\]"),
    ],
)
def test_invalid_config_raises_error(key: str, value: JsonValue, message: str) -> None:
    config = copy.deepcopy(VALID_CONFIG)
    if value is None:
        del config["remorph"][key]
    else:
        config["remorph"][key] = value
    with (
        patch("pathlib.PosixPath.read_text", return_value=yaml.dump(config)),
        pytest.raises(ValueError, match=message),
    ):
        _ = LSPEngine.from_config_path(Path("stuff"))
