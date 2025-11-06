import asyncio
import dataclasses
import locale
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import create_autospec, patch

import pytest

from databricks.connect import DatabricksSession
from databricks.labs.lsql.backends import MockBackend
from databricks.labs.lsql.core import Row
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import TranspileConfig, ValidationResult, TranspileResult
from databricks.labs.lakebridge.helpers.file_utils import dir_walk, is_sql_file
from databricks.labs.lakebridge.helpers.validation import Validator
from databricks.labs.lakebridge.transpiler.execute import (
    transpile as do_transpile,
    transpile_column_exp,
    transpile_sql,
    make_header,
)

from databricks.labs.lakebridge.transpiler.transpile_status import (
    TranspileError,
    CodeRange,
    CodePosition,
    ErrorSeverity,
    ErrorKind,
)

from databricks.labs.blueprint.installation import JsonObject
from databricks.sdk.core import Config

from databricks.labs.lakebridge.transpiler.sqlglot.sqlglot_engine import SqlglotEngine
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine

from tests.unit.conftest import path_to_resource


# pylint: disable=unspecified-encoding


def transpile(
    workspace_client: WorkspaceClient, engine: TranspileEngine, config: TranspileConfig
) -> tuple[JsonObject, list[TranspileError]]:
    return asyncio.run(do_transpile(workspace_client, engine, config))


def check_status(
    status: dict[str, Any],
    total_files_processed: int,
    total_queries_processed: int,
    analysis_error_count: int,
    parsing_error_count: int,
    validation_error_count: int,
    generation_error_count: int,
    error_file_name: Path | None,
):
    assert status is not None, "Status returned by transpile function is None"
    assert isinstance(status, dict), "Status returned by transpile function is not a dict"
    assert len(status) > 0, "Status returned by transpile function is an empty dict"
    assert (
        status["total_files_processed"] == total_files_processed
    ), "total_files_processed does not match expected value"
    assert (
        status["total_queries_processed"] == total_queries_processed
    ), "total_queries_processed does not match expected value"
    assert status["analysis_error_count"] == analysis_error_count, "analysis_error_count does not match expected value"

    assert status["parsing_error_count"] == parsing_error_count, "parsing_error_count does not match expected value"
    assert (
        status["validation_error_count"] == validation_error_count
    ), "validation_error_count does not match expected value"
    assert (
        status["generation_error_count"] == generation_error_count
    ), "generation_error_count does not match expected value"
    expected_error_file_name = str(error_file_name) if error_file_name is not None else None
    assert status["error_log_file"] == expected_error_file_name, f"error_log_file does not match {error_file_name}"


def check_error_lines(error_file_path: str, expected_errors: list[dict[str, str]]):
    pattern = r"TranspileError\(code=(?P<code>[^,]+), kind=(?P<kind>[^,]+), severity=(?P<severity>[^,]+), path='(?P<path>[^']+)', message='(?P<message>[^']+)('\))?"
    with open(Path(error_file_path)) as file:
        error_count = 0
        match_count = 0
        for line in file:
            match = re.match(pattern, line)
            if not match:
                continue
            error_count += 1
            # Extract information using group names from the pattern
            error_info = match.groupdict()
            # Perform assertions
            for expected_error in expected_errors:
                if expected_error["path"] == error_info["path"]:
                    match_count += 1
                    expected_message = expected_error["message"]
                    actual_message = error_info["message"]
                    assert (
                        expected_message in actual_message
                    ), f"Message {actual_message} does not match the expected value {expected_message}"
        assert match_count == len(expected_errors), "Not all expected errors were found"
        assert error_count == match_count, "Not all actual errors were matched"


def check_generated(input_source: Path, output_folder: Path):
    for _, _, files in dir_walk(input_source):
        for input_file in files:
            if not is_sql_file(input_file):
                continue
            relative = cast(Path, input_file).relative_to(input_source)
            transpiled = output_folder / relative
            assert transpiled.exists(), f"Could not find transpiled file {transpiled!s} for {input_file!s}"


def test_with_dir_with_output_folder_skipping_validation(
    input_source, output_folder, error_file, mock_workspace_client
):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source),
        output_folder=str(output_folder),
        error_file_path=str(error_file),
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )
    with patch('databricks.labs.lakebridge.helpers.db_sql.get_sql_backend', return_value=MockBackend()):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)
    # check the status
    check_status(status, 8, 7, 1, 2, 0, 0, error_file)
    # check errors
    expected_errors = [
        {
            "path": f"{input_source!s}/queries/query3.sql",
            "message": f"Unsupported operation found in file {input_source!s}/queries/query3.sql.",
        },
        {"path": f"{input_source!s}/queries/query4.sql", "message": "Parsing error Start:"},
        {"path": f"{input_source!s}/queries/query5.sql", "message": "Token error Start:"},
    ]
    check_error_lines(status["error_log_file"], expected_errors)
    # check generation
    check_generated(input_source, output_folder)


def test_with_file(input_source, error_file, mock_workspace_client):
    sdk_config = create_autospec(Config)
    spark = create_autospec(DatabricksSession)
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "queries" / "query1.sql"),
        output_folder=None,
        error_file_path=str(error_file),
        sdk_config=sdk_config,
        source_dialect="snowflake",
        skip_validation=False,
    )
    mock_validate = create_autospec(Validator)
    mock_validate.spark = spark
    mock_validate.validate_format_result.return_value = ValidationResult(
        """ Mock validated query """, "Mock validation error"
    )

    with (
        patch(
            'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
            return_value=MockBackend(),
        ),
        patch("databricks.labs.lakebridge.transpiler.execute.Validator", return_value=mock_validate),
    ):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)

    # check the status
    check_status(status, 1, 1, 0, 0, 1, 0, error_file)
    # check errors
    expected_errors = [{"path": f"{input_source!s}/queries/query1.sql", "message": "Mock validation error"}]
    check_error_lines(status["error_log_file"], expected_errors)


class ConfigurableTestEngine(TranspileEngine):
    """Expand test transpiler engine."""

    def __init__(
        self,
        *,
        transpiler_name: str = "test",
        supported_dialects: list[str] | None = None,
        transform: Any | None = None,  # Callable[[str], str] | None
        transpiled_code: str | None = None,
        errors: list[TranspileError] | None = None,
        file_extensions: list[str] | None = None,
        success_count: int | None = None,
    ):

        if transform is not None and transpiled_code is not None:
            raise ValueError("Cannot specify both transform and transpiled_code")

        self._transpiler_name = transpiler_name
        self._supported_dialects = supported_dialects or ["test"]
        self._transform = transform
        self._static_code = transpiled_code
        self._errors = errors or []
        self._file_extensions = file_extensions

        # Auto-calculate success_count if not provided
        if success_count is None:
            self._success_count = 0 if self._errors else 1
        else:
            self._success_count = success_count

    @property
    def transpiler_name(self) -> str:
        return self._transpiler_name

    @property
    def supported_dialects(self) -> list[str]:
        return self._supported_dialects

    async def initialize(self, config: TranspileConfig) -> None:
        assert config.source_dialect in self.supported_dialects

    async def shutdown(self) -> None:
        pass

    async def transpile(
        self, source_dialect: str, target_dialect: str, source_code: str, file_path: Path
    ) -> TranspileResult:
        assert source_dialect in self.supported_dialects

        # Determine the transpiled code
        if self._static_code is not None:
            code = self._static_code
        elif self._transform is not None:
            code = self._transform(source_code)
        else:
            # Default: identity transform
            code = source_code

        return TranspileResult(
            transpiled_code=code,
            success_count=self._success_count,
            error_list=self._errors,
        )

    def is_supported_file(self, file: Path) -> bool:
        if self._file_extensions is None:
            return True
        return file.suffix in self._file_extensions


@pytest.mark.parametrize("encoding", ["utf-32-le", "utf-32-be", "utf-16-le", "utf-16-be", "utf-8-sig", "utf-8"])
def test_transpile_unicode_files(
    encoding: str, tmp_path: Path, output_folder: Path, mock_workspace_client: WorkspaceClient
) -> None:
    # Set up the test: an input file with a specific encoding.
    sample_query = "SELECT 'All your base belong to us.\U0001f47d'"
    input_file = tmp_path / "unicode_query.sql"
    with open(input_file, "w", encoding=encoding) as f:
        # Python doesn't write the BOM with the endian-specific encodings so we add it manually.
        if encoding.endswith("-le") or encoding.endswith("-be"):
            f.write("\ufeff")
        f.write(sample_query)

    transpile_config = TranspileConfig(
        transpiler_config_path=None,
        source_dialect="identity",
        input_source=str(input_file),
        output_folder=str(output_folder),
        skip_validation=True,
    )
    # Use identity transform (returns source unchanged) for all files
    identity_engine = ConfigurableTestEngine(
        transpiler_name="identity",
        supported_dialects=["identity"],
        file_extensions=None,  # Support all file types
    )
    status, _ = transpile(mock_workspace_client, identity_engine, transpile_config)

    assert status.get("total_files_processed") == 1
    transpiled_query = (output_folder / "unicode_query.sql").read_text(encoding="utf-8")
    assert sample_query == transpiled_query


def test_with_file_with_output_folder_skip_validation(input_source, output_folder, mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "queries" / "query1.sql"),
        output_folder=str(output_folder),
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch(
        'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
        return_value=MockBackend(),
    ):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)

    # check the status
    check_status(status, 1, 1, 0, 0, 0, 0, None)


def test_with_not_a_sql_file_skip_validation(input_source, mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "file.txt"),
        output_folder=None,
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch(
        'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
        return_value=MockBackend(),
    ):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)

    # check the status
    check_status(status, 0, 0, 0, 0, 0, 0, None)


def test_with_not_existing_file_skip_validation(input_source, mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "file_not_exist.txt"),
        output_folder=None,
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )
    with pytest.raises(FileNotFoundError):
        with patch(
            'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
            return_value=MockBackend(),
        ):
            transpile(mock_workspace_client, SqlglotEngine(), config)


def test_transpile_sql(mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        source_dialect="snowflake",
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )
    query = """select col from table;"""

    with patch(
        'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
        return_value=MockBackend(
            rows={
                "EXPLAIN SELECT": [Row(plan="== Physical Plan ==")],
            }
        ),
    ):
        transpiler_result, validation_result = transpile_sql(mock_workspace_client, config, query)
        assert transpiler_result.transpiled_code == 'SELECT\n  col\nFROM table'
        assert validation_result.exception_msg is None


def test_transpile_column_exp(mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        source_dialect="snowflake",
        skip_validation=True,
        catalog_name="catalog",
        schema_name="schema",
    )
    query = ["case when col1 is null then 1 else 0 end", "col2 * 2", "current_timestamp()"]

    with patch(
        'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
        return_value=MockBackend(
            rows={
                "EXPLAIN SELECT": [Row(plan="== Physical Plan ==")],
            }
        ),
    ):
        result = transpile_column_exp(mock_workspace_client, config, query)
        assert len(result) == 3
        assert result[0][0].transpiled_code == 'CASE WHEN col1 IS NULL THEN 1 ELSE 0 END'
        assert result[1][0].transpiled_code == 'col2 * 2'
        assert result[2][0].transpiled_code == 'CURRENT_TIMESTAMP()'
        assert result[0][0].error_list == []
        assert result[1][0].error_list == []
        assert result[2][0].error_list == []
        assert result[0][1] is None
        assert result[1][1] is None
        assert result[2][1] is None


def test_with_file_with_success(input_source, mock_workspace_client):
    sdk_config = create_autospec(Config)
    spark = create_autospec(DatabricksSession)
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "queries" / "query1.sql"),
        output_folder=None,
        sdk_config=sdk_config,
        source_dialect="snowflake",
        skip_validation=False,
    )
    mock_validate = create_autospec(Validator)
    mock_validate.spark = spark
    mock_validate.validate_format_result.return_value = ValidationResult(""" Mock validated query """, None)

    with (
        patch(
            'databricks.labs.lakebridge.helpers.db_sql.get_sql_backend',
            return_value=MockBackend(),
        ),
        patch("databricks.labs.lakebridge.transpiler.execute.Validator", return_value=mock_validate),
    ):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)
        # assert the status
        check_status(status, 1, 1, 0, 0, 0, 0, None)


def test_with_input_source_none(mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=None,
        output_folder=None,
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    with pytest.raises(ValueError, match="Input SQL path is not provided"):
        transpile(mock_workspace_client, SqlglotEngine(), config)


def test_parse_error_handling(input_source, error_file, mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "queries" / "query4.sql"),
        output_folder=None,
        error_file_path=str(error_file),
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch('databricks.labs.lakebridge.helpers.db_sql.get_sql_backend', return_value=MockBackend()):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)

    # assert the status
    check_status(status, 1, 1, 0, 1, 0, 0, error_file)
    # check errors
    expected_errors = [{"path": f"{input_source}/queries/query4.sql", "message": "Parsing error Start:"}]
    check_error_lines(status["error_log_file"], expected_errors)


def test_token_error_handling(input_source, error_file, mock_workspace_client):
    config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source / "queries" / "query5.sql"),
        output_folder=None,
        error_file_path=str(error_file),
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch('databricks.labs.lakebridge.helpers.db_sql.get_sql_backend', return_value=MockBackend()):
        status, _errors = transpile(mock_workspace_client, SqlglotEngine(), config)
    # assert the status
    check_status(status, 1, 1, 0, 1, 0, 0, error_file)
    # check errors
    expected_errors = [{"path": f"{input_source}/queries/query5.sql", "message": "Token error Start:"}]
    check_error_lines(status["error_log_file"], expected_errors)


def test_server_decombines_workflow_output(mock_workspace_client, lsp_engine, transpile_config):
    with TemporaryDirectory() as output_folder:
        input_path = Path(path_to_resource("lsp_transpiler", "workflow.xml"))
        transpile_config = dataclasses.replace(
            transpile_config, input_source=input_path, output_folder=output_folder, skip_validation=True
        )
        _status, _errors = transpile(mock_workspace_client, lsp_engine, transpile_config)

        assert any(Path(output_folder).glob("*.json")), "No .json file found in output_folder"


@pytest.mark.xfail(
    locale.getpreferredencoding().lower() != "utf-8", reason="This test assumes system default encoding is UTF-8."
)
def test_encoding_error_utf8_decode_error(
    tmp_path: Path,
    output_folder: Path,
    mock_workspace_client: WorkspaceClient,
) -> None:
    """Test UnicodeDecodeError handling when files should be UTF-8 but aren't."""
    # Create a file with Latin-1 encoding containing non-ASCII characters
    # When read_text() tries to decode this as UTF-8, it will fail with UnicodeDecodeError
    problematic_file = tmp_path / "input" / "latin1_file.sql"
    problematic_file.parent.mkdir(parents=True)
    # Write Latin-1 encoded content with non-ASCII characters that will fail as UTF-8
    problematic_file.write_text("SELECT 'h\u00e9llo w\u00f6rld' AS greeting;", encoding="latin-1")

    transpile_config = TranspileConfig(
        transpiler_config_path=None,
        source_dialect="identity",
        input_source=str(problematic_file),
        output_folder=str(output_folder),
        skip_validation=True,
    )

    # This reads files using the default system encoding, and this test assumes it's UTF-8.
    identity_engine = ConfigurableTestEngine(
        transpiler_name="identity",
        supported_dialects=["identity"],
        file_extensions=None,
    )
    status, errors = transpile(mock_workspace_client, identity_engine, transpile_config)

    # Verify error handling
    assert status.get("total_files_processed") == 1  # File was processed (but failed)
    assert status.get("total_queries_processed") == 0  # No queries successfully processed
    [only_error] = errors
    assert only_error.code == "encoding-error"
    assert only_error.severity == ErrorSeverity.ERROR
    assert "codec can't decode" in only_error.message


def test_encoding_error_lookup_error(
    tmp_path: Path,
    output_folder: Path,
    mock_workspace_client: WorkspaceClient,
) -> None:
    """Test LookupError handling when XML file declares an unknown encoding."""
    # Create an XML file that declares an invalid encoding name
    # When read_text() tries to use this encoding, it will fail with LookupError
    xml_file = tmp_path / "input" / "invalid_encoding.xml"
    xml_file.parent.mkdir(parents=True)
    # XML declaration with non-existent encoding - will trigger LookupError
    xml_file.write_text("<?xml version='1.0' encoding='definitely-invalid-codec'?><empty_root/>", encoding="utf-8")

    transpile_config = TranspileConfig(
        transpiler_config_path=None,
        source_dialect="identity",
        input_source=str(xml_file),
        output_folder=str(output_folder),
        skip_validation=True,
    )

    identity_engine = ConfigurableTestEngine(
        transpiler_name="identity",
        supported_dialects=["identity"],
        file_extensions=None,
    )
    status, errors = transpile(mock_workspace_client, identity_engine, transpile_config)

    # Verify error handling
    assert status.get("total_files_processed") == 1  # File was processed (but failed)
    assert status.get("total_queries_processed") == 0  # No queries successfully processed
    [only_error] = errors
    assert only_error.code == "encoding-error"
    assert only_error.severity == ErrorSeverity.ERROR
    assert "encoding" in only_error.message


def test_encoding_error_continues_with_other_files(
    input_source: Path,
    output_folder: Path,
    mock_workspace_client: WorkspaceClient,
) -> None:
    """Test that encoding errors on one file don't prevent processing other files."""
    # Add a problematic file to the existing input_source directory
    # This tests the real-world scenario of mixed good/bad files in a directory
    problematic_file = input_source / "problematic.sql"
    problematic_file.write_text("SELECT 'bad encoding h\u00e9r\u00e9' AS test;", encoding="latin-1")

    transpile_config = TranspileConfig(
        transpiler_config_path="sqlglot",
        input_source=str(input_source),
        output_folder=str(output_folder),
        sdk_config=None,
        source_dialect="snowflake",
        skip_validation=True,
    )

    status, errors = transpile(mock_workspace_client, SqlglotEngine(), transpile_config)

    # Should process existing good files successfully despite the problematic one
    files_processed = status.get("total_files_processed", 0)
    queries_processed = status.get("total_queries_processed", 0)
    assert isinstance(files_processed, int) and isinstance(queries_processed, int)
    assert files_processed > 1  # Multiple files were processed
    assert queries_processed > 0  # Some files had successful queries
    assert files_processed > queries_processed  # At least one file failed due to encoding error

    # Should have encoding errors for the problematic file
    encoding_errors = [e for e in errors if e.code == "encoding-error"]
    [only_encoding_error] = encoding_errors
    assert "problematic.sql" in str(only_encoding_error.path)


def test_make_header_with_no_diagnostics():
    path = Path("/tmp/path/to/input")
    diagnostics = []
    header = make_header(path, diagnostics)

    assert (
        header
        == """/*
    Successfully transpiled from /tmp/path/to/input
*/
"""
    )


def test_make_header_with_one_error():
    path = Path("/tmp/path/to/input")
    diagnostics = [
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.ERROR,
            path,
            "this is an error message",
            CodeRange(start=CodePosition(0, 0), end=CodePosition(1, 0)),
        )
    ]
    header = make_header(path, diagnostics)

    assert (
        header
        == """/*
    Failed transpilation of /tmp/path/to/input

    The following errors were found while transpiling:
      - [7:1] this is an error message
*/
"""
    )


def test_make_header_with_one_warning():
    path = Path("/tmp/path/to/input")
    diagnostics = [
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.WARNING,
            path,
            "this is a warning",
            CodeRange(start=CodePosition(0, 0), end=CodePosition(1, 0)),
        )
    ]
    header = make_header(path, diagnostics)

    assert (
        header
        == """/*
    Successfully transpiled from /tmp/path/to/input

    The following warnings were found while transpiling:
      - [7:1] this is a warning
*/
"""
    )


def test_make_header_with_one_repeated_error():
    path = Path("/tmp/path/to/input")
    diagnostics = [
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.ERROR,
            path,
            "this is an error message",
            CodeRange(start=CodePosition(0, 0), end=CodePosition(1, 0)),
        ),
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.ERROR,
            path,
            "this is an error message",
            CodeRange(start=CodePosition(1, 0), end=CodePosition(2, 0)),
        ),
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.ERROR,
            path,
            "this is an error message",
            CodeRange(start=CodePosition(2, 0), end=CodePosition(3, 0)),
        ),
    ]
    header = make_header(path, diagnostics)

    assert (
        header
        == """/*
    Failed transpilation of /tmp/path/to/input

    The following errors were found while transpiling:
      - this is an error message
          Occurred 3 times at the following positions: [8:1], [9:1], [10:1]
*/
"""
    )


def test_make_header_with_one_repeated_warning():
    path = Path("/tmp/path/to/input")
    diagnostics = [
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.WARNING,
            path,
            "this is a warning",
            CodeRange(start=CodePosition(0, 0), end=CodePosition(1, 0)),
        ),
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.WARNING,
            path,
            "this is a warning",
            CodeRange(start=CodePosition(1, 0), end=CodePosition(2, 0)),
        ),
        TranspileError(
            None,
            ErrorKind.INTERNAL,
            ErrorSeverity.WARNING,
            path,
            "this is a warning",
            CodeRange(start=CodePosition(2, 0), end=CodePosition(3, 0)),
        ),
    ]
    header = make_header(path, diagnostics)

    assert (
        header
        == """/*
    Successfully transpiled from /tmp/path/to/input

    The following warnings were found while transpiling:
      - this is a warning
          Occurred 3 times at the following positions: [8:1], [9:1], [10:1]
*/
"""
    )


def test_transpiled_code_output_on_parsing_error(tmp_path: Path, mock_workspace_client: WorkspaceClient):
    """Test that transpiled code is output even when parsing errors occur."""
    input_file = tmp_path / "test.sql"
    output_folder = tmp_path / "output"
    output_folder.mkdir()

    original_sql = "SELECT NUMBER_COL, VARCHAR_COL FROM my_table"
    input_file.write_text(original_sql)
    transpiled_sql = "SELECT DECIMAL_COL, STRING_COL FROM my_table"

    parsing_error = TranspileError(
        code="PARSE_ERROR",
        kind=ErrorKind.PARSING,
        severity=ErrorSeverity.ERROR,
        path=input_file,
        message="Parsing error: unexpected token",
        range=CodeRange(start=CodePosition(0, 0), end=CodePosition(0, 10)),
    )

    # Mock engine returns static transpiled code with parsing errors
    mock_engine = ConfigurableTestEngine(
        transpiler_name="mock",
        supported_dialects=["snowflake", "tsql"],
        transpiled_code=transpiled_sql,
        errors=[parsing_error],
        file_extensions=[".sql"],
    )

    config = TranspileConfig(
        transpiler_config_path="mock",
        input_source=str(input_file),
        output_folder=str(output_folder),
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch("databricks.labs.lakebridge.helpers.db_sql.get_sql_backend", return_value=MockBackend()):
        status, errors = transpile(mock_workspace_client, mock_engine, config)

    output_file = output_folder / "test.sql"
    assert output_file.exists(), "Output file was not created"
    output_content = output_file.read_text()

    assert output_content == transpiled_sql, f"Expected transpiled code '{transpiled_sql}' but got '{output_content}'"
    assert output_content != original_sql, "Output should not be the original SQL"

    assert len(errors) == 1
    assert errors[0].kind == ErrorKind.PARSING
    assert status["parsing_error_count"] == 1


def test_transpiled_code_output_without_errors(tmp_path: Path, mock_workspace_client: WorkspaceClient):
    """Test that transpiled code is output correctly when no errors occur."""
    input_file = tmp_path / "success.sql"
    output_folder = tmp_path / "output"
    output_folder.mkdir()

    original_sql = "CREATE TABLE test (id NUMBER, name VARCHAR(100))"
    input_file.write_text(original_sql)
    transpiled_sql = "CREATE TABLE test (id DECIMAL(38, 0), name STRING)"

    # Mock engine returns static transpiled code with no errors
    mock_engine = ConfigurableTestEngine(
        transpiler_name="mock",
        supported_dialects=["snowflake", "tsql"],
        transpiled_code=transpiled_sql,
        errors=[],
        file_extensions=[".sql"],
    )

    config = TranspileConfig(
        transpiler_config_path="mock",
        input_source=str(input_file),
        output_folder=str(output_folder),
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch("databricks.labs.lakebridge.helpers.db_sql.get_sql_backend", return_value=MockBackend()):
        status, errors = transpile(mock_workspace_client, mock_engine, config)

    output_file = output_folder / "success.sql"
    assert output_file.exists(), "Output file was not created"
    output_content = output_file.read_text()

    assert output_content == transpiled_sql, f"Expected '{transpiled_sql}' but got '{output_content}'"
    assert output_content != original_sql, "Output should be transpiled, not original"

    assert len(errors) == 0
    assert status["parsing_error_count"] == 0
    assert status["total_files_processed"] == 1


def test_empty_transpiled_code_with_parsing_error(tmp_path: Path, mock_workspace_client: WorkspaceClient):
    """Test handling when transpiled_code is empty/None during parsing error."""
    input_file = tmp_path / "error.sql"
    output_folder = tmp_path / "output"
    output_folder.mkdir()

    original_sql = "INVALID SQL SYNTAX !!!"
    input_file.write_text(original_sql)

    parsing_error = TranspileError(
        code="PARSE_ERROR",
        kind=ErrorKind.PARSING,
        severity=ErrorSeverity.ERROR,
        path=input_file,
        message="Fatal parsing error",
        range=None,
    )

    # Mock engine returns empty transpiled code with parsing errors
    mock_engine = ConfigurableTestEngine(
        transpiler_name="mock",
        supported_dialects=["snowflake", "tsql"],
        transpiled_code="",
        errors=[parsing_error],
        file_extensions=[".sql"],
    )

    config = TranspileConfig(
        transpiler_config_path="mock",
        input_source=str(input_file),
        output_folder=str(output_folder),
        source_dialect="snowflake",
        skip_validation=True,
    )

    with patch("databricks.labs.lakebridge.helpers.db_sql.get_sql_backend", return_value=MockBackend()):
        status, errors = transpile(mock_workspace_client, mock_engine, config)

    output_file = output_folder / "error.sql"
    assert output_file.exists(), "Output file should be created even with errors"
    output_content = output_file.read_text()

    assert output_content == "", "Output should be empty string when transpilation fails completely"

    assert len(errors) == 1
    assert errors[0].kind == ErrorKind.PARSING
    assert status["parsing_error_count"] == 1


def test_transpiled_output_nested_directories(tmp_path: Path, mock_workspace_client: WorkspaceClient) -> None:
    """Verify that nested directory hierarchies are preserved properly."""
    # Setup an input hierarchy that we expect to be preserved.
    input_path = tmp_path / "input"
    output_path = tmp_path / "output"
    sources = {
        input_path / "file_1.sql": "select 'file_1';",
        input_path / "outer" / "inner" / "file_2.sql": "select 'file_2';",
    }
    for path, source in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    # Set up a mock engine for exercising the processing loop.
    mock_engine = ConfigurableTestEngine(
        supported_dialects=["sql"],
        file_extensions=[".sql"],
        transform=lambda sql: f"-- Transformed!\n{sql}",
    )
    config = TranspileConfig(
        transpiler_config_path="mock",
        input_source=str(input_path),
        output_folder=str(output_path),
        source_dialect="sql",
        skip_validation=True,
    )

    # Perform the conversion.
    status, errors = transpile(mock_workspace_client, mock_engine, config)
    assert status["total_files_processed"] == 2 and not errors

    # Verify the conversion occurred and the results.
    for path, source in sources.items():
        relative_path = path.relative_to(input_path)
        expected_output_path = output_path / relative_path
        assert expected_output_path.is_file(), f"Missing expected output path: {path} -> {expected_output_path}"
        expected_output = f"-- Transformed!\n{source}"
        actual_output = expected_output_path.read_text(encoding="utf-8")
        assert actual_output == expected_output
