import os
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.install import TranspilerInstaller
from databricks.labs.lakebridge.transpiler.execute import transpile
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine

logger = logging.getLogger(__name__)


# TODO use artifact from PyPI once 0.1.5 is published
@pytest.mark.skipif(
    os.environ.get("CI", "false") == "true", reason="Skipping in CI Since LSP crashes on Acceptance tests"
)
async def test_transpiles_informatica_with_sparksql(ws, bladebridge_artifact):
    with TemporaryDirectory() as tmpdir:
        with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
            await _transpile_informatica_with_sparksql(ws, bladebridge_artifact)


async def _transpile_informatica_with_sparksql(ws: Any, bladebridge_artifact: Path):
    bladebridge = TranspilerInstaller.transpilers_path() / "bladebridge"
    assert not bladebridge.exists()
    TranspilerInstaller.install_from_pypi("bladebridge", "databricks-bb-plugin", bladebridge_artifact)
    # check execution
    config_path = TranspilerInstaller.transpilers_path() / "bladebridge" / "lib" / "config.yml"
    lsp_engine = LSPEngine.from_config_path(config_path)
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "informatica"
    with TemporaryDirectory() as output_folder:
        transpile_config = TranspileConfig(
            transpiler_config_path=str(config_path),
            source_dialect="informatica (desktop edition)",
            input_source=str(input_source),
            output_folder=output_folder,
            skip_validation=True,
            catalog_name="catalog",
            schema_name="schema",
            transpiler_options={"target-tech": "SPARKSQL"},
        )
        result = await transpile(ws, lsp_engine, transpile_config)
        logger.info(result)
