import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import ClassVar, final

import pytest

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine, LanguageClient, logger as lsp_logger


@final
class LSPServerLogs:
    # The log-level at which the LSP engine writes out stderr lines from the LSP server.
    stderr_log_level: ClassVar[int] = logging.DEBUG
    # The function name from the stderr lines are logged, to help filter out other logs.
    stderr_log_function: ClassVar[str] = "pipe_stream"

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
    """Verify that output from the LSP engine is captured as logs at INFO level."""
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


class MockStreamReader(asyncio.StreamReader):
    """Mock asyncio.StreamReader that returns pre-configured chunks."""

    def __init__(self, data_chunks: Sequence[bytes]) -> None:
        super().__init__()
        # Chunks represent data that could be returned on successive reads, mimicking the nature of non-blocking I/O
        # where partial data may be returned. The chunk boundaries represent the splits where partial data is returned.
        self._remaining_data = data_chunks

    async def read(self, n: int = -1) -> bytes:
        match n:
            case -1:
                # Read all remaining data.
                data = b"".join(self._remaining_data)
                self._remaining_data = []
            case 0:
                # Empty read.
                data = b""
            case max_read if max_read > 0:
                # Read up to n, but only from the first chunk.
                match self._remaining_data:
                    case []:
                        data = b""
                    case [head, *tail]:
                        if len(head) <= max_read:
                            data = head
                            self._remaining_data = tail
                        else:
                            data = head[:max_read]
                            self._remaining_data = [head[max_read:], *tail]
            case _:
                raise ValueError(f"Unsupported read size: {n}")
        return data


async def assert_pipe_stream_chunks_produce_logs(
    data_chunks: Sequence[bytes],
    expected_messages: Sequence[str],
    caplog: pytest.LogCaptureFixture,
    *,
    limit: int = 128,
) -> None:
    stream = MockStreamReader(data_chunks)
    with caplog.at_level(LSPServerLogs.stderr_log_level):
        await LanguageClient.pipe_stream(stream=stream, limit=limit)

    messages = tuple(LSPServerLogs.get_pipe_stream_logs(caplog))
    assert messages == expected_messages


async def test_pipe_stream_normal_lines(caplog: pytest.LogCaptureFixture) -> None:
    """Verify the simple case of each line fitting within the limit: one line per log message."""
    data_chunks = (b"first line\n", b"second line\n", b"third line\n")
    expected_messages = ("first line", "second line", "third line")
    await assert_pipe_stream_chunks_produce_logs(data_chunks, expected_messages, caplog)


async def test_pipe_stream_whitespace_handling(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that trailing whitespace is stripped, and that empty log-lines are skipped."""
    data_chunks = (b"  first line  \r\n", b"\tsecond line\t\r\n", b" \t \r\n", b"\n", b"last\tproper\tline\n", b" \t ")
    expected_messages = ("  first line", "\tsecond line", "last\tproper\tline")
    await assert_pipe_stream_chunks_produce_logs(data_chunks, expected_messages, caplog)


@pytest.mark.parametrize(
    ("data_chunks", "expected_messages"),
    (
        # Note: limit for all examples is 10.
        # Single line split over 2 reads.
        ((b"1234567", b"89\n"), ("123456789",)),
        # Single read, exactly on the limit.
        ((b"123456789\n",), ("123456789",)),
        # Single read, exactly on the minimum limit to trigger premature flush.
        ((b"1234567890",), ("1234567890[..?]",)),
        # Maximum line length.
        ((b"123456789", b"123456789\n"), ("1234567891[..?]", "23456789")),
        # Multiple lines in one read, with existing data from the previous read.
        ((b"1", b"12\n45\n78\n0", b"12\n"), ("112", "45", "78", "012")),
        # A very long line, with some existing data in the buffer, and leaving some remainder.
        (
            (b"12", b"3456789012" b"3456789012" b"3456789012" b"34567890\n1234"),
            ("1234567890[..?]", "1234567890[..?]", "1234567890[..?]", "1234567890[..?]", "1234 <missing EOL at EOF>"),
        ),
    ),
)
async def test_pipe_stream_line_exceeds_limit(
    data_chunks: Sequence[bytes],
    expected_messages: Sequence[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that line buffering and splitting is handled, including if a line is (much!) longer than the limit."""
    await assert_pipe_stream_chunks_produce_logs(data_chunks, expected_messages, caplog, limit=10)


async def test_pipe_stream_incomplete_line_at_eof(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that an incomplete line at EOF is logged."""
    data_chunks = (b"normal_line\n", b"incomplete_line")
    expected_messages = ("normal_line", "incomplete_line <missing EOL at EOF>")
    await assert_pipe_stream_chunks_produce_logs(data_chunks, expected_messages, caplog)


async def test_pipe_stream_invalid_utf8(caplog: pytest.LogCaptureFixture) -> None:
    """Test invalid UTF-8 sequences are replaced with replacement character."""
    data_chunks = (
        # A line with invalid UTF-8 bytes in it.
        b"bad[\xc0\xc0]utf8\n",
        # A long line, that will be split across the utf-8 sequence.
        "123456789abcd\U0001f596efgh\n".encode("utf-8"),
    )
    expected_messages = ("bad[\ufffd\ufffd]utf8", "123456789abcd\ufffd[..?]", "\ufffdefgh")
    await assert_pipe_stream_chunks_produce_logs(data_chunks, expected_messages, caplog, limit=16)
