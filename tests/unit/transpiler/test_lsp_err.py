import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine


@asynccontextmanager
async def run_lsp_server() -> AsyncGenerator[LSPEngine, None]:
    """Run the LSP server and yield the LSPEngine instance."""
    config_path = Path(__file__).parent.parent.parent / "resources" / "lsp_transpiler" / "lsp_config.yml"
    lsp_engine = LSPEngine.from_config_path(config_path)
    config = TranspileConfig(
        transpiler_config_path="transpiler_config_path",
        source_dialect="source_dialect",
        input_source="input_source",
    )
    await lsp_engine.initialize(config)
    try:
        yield lsp_engine
    finally:
        await lsp_engine.shutdown()


@pytest.mark.asyncio
async def test_stderr_captured_as_logs(caplog) -> None:
    """Verify that output from the LSP engine is captured as logs at INFO level."""
    # The LSP engine logs a message to stderr when it starts; look for that message in the logs.
    with caplog.at_level(logging.INFO):
        async with run_lsp_server() as lsp_engine:
            assert lsp_engine.is_alive

    expected = (LSPEngine.__module__, logging.INFO, "Running LSP Test Server\u2026")
    assert expected in caplog.record_tuples


@pytest.mark.asyncio
async def test_stderr_non_utf8_captured(caplog) -> None:
    """Verify that output from the LSP engine on stderr is captured even if it doesn't decode as UTF-8."""
    with caplog.at_level(logging.INFO):
        async with run_lsp_server() as lsp_engine:
            assert lsp_engine.is_alive

    # U+FFFD is the Unicode replacement character, when invalid UTF-8 is encountered.
    expected = (LSPEngine.__module__, logging.INFO, "Some bytes that are invalid UTF-8: [\ufffd\ufffd]")
    assert expected in caplog.record_tuples
