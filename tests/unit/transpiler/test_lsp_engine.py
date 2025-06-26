import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
import pytest

from lsprotocol.types import TextEdit, Range, Position

from databricks.labs.blueprint.paths import read_text
from databricks.labs.blueprint.wheels import ProductInfo

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.errors.exceptions import IllegalStateException
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import ChangeManager, LSPEngine
from databricks.labs.lakebridge.transpiler.transpile_status import TranspileError, ErrorSeverity, ErrorKind

from tests.unit.conftest import path_to_resource


async def test_initializes_lsp_server(lsp_engine, transpile_config):
    assert not lsp_engine.is_alive
    await lsp_engine.initialize(transpile_config)
    sleep(3)
    assert lsp_engine.is_alive


async def test_initializes_lsp_server_only_once(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    with pytest.raises(IllegalStateException):
        await lsp_engine.initialize(transpile_config)


async def test_shuts_lsp_server_down(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    await lsp_engine.shutdown()
    assert not lsp_engine.is_alive


async def test_sets_env_variables(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    assert "SOME_ENV=abc" in log  # see environment in lsp_transpiler/config.yml


async def test_passes_options(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    assert "experimental=True" in log  # see environment in lsp_transpiler/config.yml


async def test_passes_extra_args(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    assert "--stuff=12" in log  # see command_line in lsp_transpiler/config.yml


async def test_passes_log_level(lsp_engine, transpile_config):
    logging.getLogger("databricks").setLevel(logging.INFO)
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    assert "--log_level=INFO" in log  # see command_line in lsp_transpiler/config.yml


async def test_receives_config(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    assert "dialect=snowflake" in log


async def test_receives_client_info(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    product_info = ProductInfo.from_class(type(lsp_engine))
    # The product version can include a suffix of the form +{rev}{timestamp}. The timestamp for this process won't match
    # that of the LSP server under test, so we strip it off the string that we will hunt for in the log.
    (stripped_product_version, *_) = product_info.version().split("+")
    expected_client_info = f"client-info={product_info.product_name()}/{stripped_product_version}"
    assert expected_client_info in log


async def test_receives_process_id(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    log = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log")).read_text("utf-8")
    expected_process_id = f"client-process-id={os.getpid()}"
    assert expected_process_id in log


async def test_server_has_transpile_capability(lsp_engine, transpile_config):
    await lsp_engine.initialize(transpile_config)
    assert lsp_engine.server_has_transpile_capability


async def read_log(marker: str):
    log_path = Path(path_to_resource("lsp_transpiler", "test-lsp-server.log"))
    # need to give time to child process
    for _ in range(1, 10):
        await asyncio.sleep(0.1)
        log = log_path.read_text("utf-8")
        if marker in log:
            break
    return log_path.read_text("utf-8")


async def test_server_fetches_workspace_file(lsp_engine, transpile_config):
    sample_path = Path(path_to_resource("lsp_transpiler", "workspace_file.yml"))
    await lsp_engine.initialize(transpile_config)
    log = await read_log("fetch-document-uri")
    assert f"fetch-document-uri={sample_path.as_uri()}" in log


async def test_server_loads_document(lsp_engine: LSPEngine, transpile_config: TranspileConfig) -> None:
    sample_path = Path(path_to_resource("lsp_transpiler", "source_stuff.sql"))
    await lsp_engine.initialize(transpile_config)
    lsp_engine.open_document(sample_path, read_text(sample_path))
    log = await read_log("open-document-uri")
    assert f"open-document-uri={sample_path.as_uri()}" in log


async def test_server_closes_document(lsp_engine: LSPEngine, transpile_config: TranspileConfig) -> None:
    sample_path = Path(path_to_resource("lsp_transpiler", "source_stuff.sql"))
    await lsp_engine.initialize(transpile_config)
    lsp_engine.open_document(sample_path, read_text(sample_path))
    lsp_engine.close_document(sample_path)
    log = await read_log("close-document-uri")
    assert f"close-document-uri={sample_path.as_uri()}" in log


async def test_server_transpiles_document(lsp_engine, transpile_config):
    sample_path = Path(path_to_resource("lsp_transpiler", "source_stuff.sql"))
    await lsp_engine.initialize(transpile_config)
    result = await lsp_engine.transpile(
        transpile_config.source_dialect, "databricks", sample_path.read_text(encoding="utf-8"), sample_path
    )
    await lsp_engine.shutdown()
    transpiled_path = Path(path_to_resource("lsp_transpiler", "transpiled_stuff.sql"))
    assert result.transpiled_code == transpiled_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "source, changes, expected",
    [
        ("", [], ""),
        ("\n", [], "\n"),
        ("a", [], "a"),
        ("a\n", [], "a\n"),
        ("a\n", [TextEdit(Range(Position(0, 0), Position(1, 1)), "b\n")], "b\n"),
        ("a\n", [TextEdit(Range(Position(0, 0), Position(0, 1)), "b")], "b\n"),
        ("a\nb\nc\n", [TextEdit(Range(Position(0, 0), Position(1, 1)), "x")], "x\nc\n"),
        ("abc", [TextEdit(Range(Position(0, 1), Position(0, 2)), "x")], "axc"),
        ("abc\ndef\nghi", [TextEdit(Range(Position(0, 2), Position(2, 1)), "x\ny")], "abx\nyhi"),
        ("abbcccdddd", [TextEdit(Range(Position(0, 0), Position(1, 0)), "1\n22\n333\n4444\n")], "1\n22\n333\n4444\n"),
    ],
)
def test_change_mgr_replaces_text(source, changes, expected):
    result = ChangeManager.apply(source, changes, [], Path())
    assert result.transpiled_code == expected


@pytest.mark.parametrize(
    "resource, errors",
    [
        ("source_stuff.sql", []),
        (
            "no_transpile.sql",
            [
                TranspileError(
                    "NOT_REQUIRED",
                    ErrorKind.GENERATION,
                    ErrorSeverity.INFO,
                    Path("no_transpile.sql"),
                    "No transpilation required",
                )
            ],
        ),
        (
            "unsupported_lca.sql",
            [
                TranspileError(
                    "UNSUPPORTED_LCA",
                    ErrorKind.ANALYSIS,
                    ErrorSeverity.ERROR,
                    Path("unsupported_lca.sql"),
                    "LCA conversion not supported",
                )
            ],
        ),
        (
            "internal.sql",
            [
                TranspileError(
                    "SOME_ERROR_CODE",
                    ErrorKind.INTERNAL,
                    ErrorSeverity.WARNING,
                    Path("internal.sql"),
                    "Something went wrong",
                )
            ],
        ),
    ],
)
async def test_client_translates_diagnostics(lsp_engine, transpile_config, resource, errors):
    sample_path = Path(path_to_resource("lsp_transpiler", resource))
    await lsp_engine.initialize(transpile_config)
    result = await lsp_engine.transpile(
        transpile_config.source_dialect, "databricks", sample_path.read_text(encoding="utf-8"), sample_path
    )
    await lsp_engine.shutdown()
    actual = [dataclasses.replace(error, path=Path(error.path.name), range=None) for error in result.error_list]
    assert actual == errors


async def test_server_transpiles_workflow(lsp_engine, transpile_config):
    with TemporaryDirectory() as output_folder:
        transpile_config = dataclasses.replace(transpile_config, output_folder=output_folder)
        sample_path = Path(path_to_resource("lsp_transpiler", "workflow.xml"))
        await lsp_engine.initialize(transpile_config)
        result = await lsp_engine.transpile(
            transpile_config.source_dialect, "databricks", sample_path.read_text(encoding="utf-8"), sample_path
        )
        await lsp_engine.shutdown()
        assert result.transpiled_code.startswith("Content-Type: multipart/mixed; boundary=")
