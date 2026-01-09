import logging
import re
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import ClassVar, final

import pytest

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import (
    LSPEngine,
    logger as lsp_logger,
)


@final
class LSPServerLogs:
    # The log-level at which the LSP engine writes out stderr lines from the LSP server.
    stderr_log_level: ClassVar[int] = logging.DEBUG
    # The function name from the stderr lines are logged, to help filter out other logs.
    stderr_log_function: ClassVar[str] = "pipe_stderr"

    def __init__(self, caplog: pytest.LogCaptureFixture):
        self._caplog = caplog

    @contextmanager
    def capture(self) -> Generator[None]:
        current_log_level = lsp_logger.getEffectiveLevel()
        includes_info = current_log_level if current_log_level < self.stderr_log_level else self.stderr_log_level
        with self._caplog.at_level(includes_info):
            yield

    def log_lines(self) -> Sequence[str]:
        return self.get_pipe_stream_logs(self._caplog)

    @classmethod
    def get_pipe_stream_logs(cls, caplog: pytest.LogCaptureFixture) -> Sequence[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.levelno == cls.stderr_log_level
            and record.name == lsp_logger.name
            and record.funcName == cls.stderr_log_function
        ]


@pytest.fixture
def capture_lsp_server_logs(caplog: pytest.LogCaptureFixture) -> LSPServerLogs:
    return LSPServerLogs(caplog)


@asynccontextmanager
async def run_lsp_server() -> AsyncGenerator[LSPEngine]:
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
async def test_stderr_captured_as_logs(capture_lsp_server_logs: LSPServerLogs) -> None:
    """Verify that output from the LSP engine is captured as logs."""
    # The LSP engine logs a message to stderr when it starts; look for that message in the logs.
    with capture_lsp_server_logs.capture():
        async with run_lsp_server() as lsp_engine:
            assert lsp_engine.is_alive

    assert "Running LSP Test Server\u2026" in capture_lsp_server_logs.log_lines()


@pytest.mark.asyncio
async def test_stderr_non_utf8_captured(capture_lsp_server_logs: LSPServerLogs) -> None:
    """Verify that output from the LSP engine on stderr is captured even if it doesn't decode as UTF-8."""
    with capture_lsp_server_logs.capture():
        async with run_lsp_server() as lsp_engine:
            assert lsp_engine.is_alive

    # U+FFFD is the Unicode replacement character, when invalid UTF-8 is encountered.
    assert "Some bytes that are invalid UTF-8: [\ufffd\ufffd]" in capture_lsp_server_logs.log_lines()


async def test_stderr_with_long_lines(
    tmp_path: Path, lsp_engine: LSPEngine, transpile_config: TranspileConfig, capture_lsp_server_logs: LSPServerLogs
) -> None:
    """Test our handling of really long stderr lines from the LSP server."""
    # Testing method is based on the LSP server logging the query to stderr, which we can make very very very long.
    padding_size = 100_000
    sample_path = tmp_path / "large_file.sql"
    sample_code = f"select '{'x' * padding_size}';"
    sample_path.write_text(sample_code, encoding="utf-8")
    assert (source_dialect := transpile_config.source_dialect) is not None

    await lsp_engine.initialize(transpile_config)

    with capture_lsp_server_logs.capture():
        result = await lsp_engine.transpile(source_dialect, "databricks", sample_code, sample_path)

    await lsp_engine.shutdown()

    lsp_server_logs = capture_lsp_server_logs.log_lines()

    expected_code = sample_code.upper()
    assert result.transpiled_code == expected_code

    # The logs, being long, will be split. Rather than joint we will look for the query prefix/suffix start/end and
    # count the 'x's.
    log_text = "\n".join(lsp_server_logs)
    sample_matcher = re.compile(r"select '(?P<padding>.*?)';", re.DOTALL | re.MULTILINE)
    assert (sample_match := sample_matcher.search(log_text)) is not None
    assert sample_match.group("padding").count("x") == padding_size
    expected_matcher = re.compile(r"SELECT '(?P<padding>.*?)';", re.DOTALL | re.MULTILINE)
    assert (expected_match := expected_matcher.search(log_text)) is not None
    assert expected_match.group("padding").count("X") == padding_size
