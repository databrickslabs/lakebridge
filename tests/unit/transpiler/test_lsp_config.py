from pathlib import Path
from typing import Any
from unittest.mock import patch

import copy
import pytest
import yaml

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
    - SOME_ENV: abc
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
        ("version", None, "Unsupported transpiler config version"),
        ("version", 0, "Unsupported transpiler config version"),
        ("name", None, "Missing 'name' entry"),
        ("name", "", "Missing 'name' entry"),
        ("dialects", None, "Missing 'dialects' entry"),
        ("dialects", [], "Missing 'dialects' entry"),
        ("command_line", None, "Missing 'command_line' entry"),
        ("command_line", [], "Missing 'command_line' entry"),
    ],
)
def test_invalid_config_raises_error(key, value, message):
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
