import asyncio
import dataclasses
import logging
from email import policy
from email.message import Message
from email.parser import Parser as EmailParser
from pathlib import Path
from typing import cast
import itertools

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.blueprint.paths import read_text
from databricks.labs.lakebridge.__about__ import __version__
from databricks.labs.lakebridge.config import (
    TranspileConfig,
    TranspileResult,
    ValidationResult,
)
from databricks.labs.lakebridge.helpers import db_sql
from databricks.labs.lakebridge.helpers.execution_time import timeit
from databricks.labs.lakebridge.helpers.file_utils import (
    dir_walk,
    make_dir,
)
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine
from databricks.labs.lakebridge.transpiler.transpile_status import (
    TranspileStatus,
    TranspileError,
    ErrorKind,
    ErrorSeverity,
)
from databricks.labs.lakebridge.helpers.validation import Validator
from databricks.labs.lakebridge.transpiler.sqlglot.sqlglot_engine import SqlglotEngine
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class TranspilingContext:
    config: TranspileConfig
    validator: Validator | None
    transpiler: TranspileEngine
    input_path: Path
    output_folder: Path
    output_path: Path | None = None
    source_code: str | None = None
    transpiled_code: str | None = None


async def _process_one_file(context: TranspilingContext) -> tuple[int, list[TranspileError]]:

    logger.debug(f"Started processing file: {context.input_path!s}")

    if not context.config.source_dialect:
        error = TranspileError(
            code="no-source-dialect-specified",
            kind=ErrorKind.INTERNAL,
            severity=ErrorSeverity.ERROR,
            path=context.input_path,
            message="No source dialect specified",
        )
        return 0, [error]

    source_code = read_text(context.input_path)
    context = dataclasses.replace(context, source_code=source_code)

    transpile_result = await _transpile(
        context.transpiler,
        str(context.config.source_dialect),
        context.config.target_dialect,
        source_code,
        context.input_path,
    )

    # Potentially expensive, only evaluate if debug is enabled
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Finished transpiling file: {context.input_path} (result: {transpile_result})")

    error_list = list(transpile_result.error_list)
    context = dataclasses.replace(context, transpiled_code=transpile_result.transpiled_code)

    output_path = cast(Path, context.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_combined_result(transpile_result):
        _process_combined_result(context, error_list)
    else:
        _process_single_result(context, error_list)

    return transpile_result.success_count, error_list


def _is_combined_result(result: TranspileResult):
    return result.transpiled_code.startswith("Content-Type: multipart/mixed; boundary=")


def _process_combined_result(context: TranspilingContext, _error_list: list[TranspileError]) -> None:
    # TODO error handling
    # Added policy to process quoted-printable encoded
    parser = EmailParser(policy=policy.default)
    transpiled_code: str = cast(str, context.transpiled_code)
    message: Message = parser.parsestr(transpiled_code)
    for part in message.walk():
        _process_combined_part(context, part)


def _process_combined_part(context: TranspilingContext, part: Message) -> None:
    if part.get_content_type() != "text/plain":
        return  # TODO Need to handle other content types, e.g., text/binary, application/json, etc.
    filename = part.get_filename()
    payload = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"
    if isinstance(payload, bytes):
        content = payload.decode(charset)
    else:
        content = str(payload)
    logger.debug(f"Processing file: {filename}")

    if not filename:
        return  # TODO Raise exception!!!!
    filename = Path(filename).name
    folder = context.output_folder
    segments = filename.split("/")
    for segment in segments[:-1]:
        folder = folder / segment
        folder.mkdir(parents=True, exist_ok=True)
    output = folder / segments[-1]
    logger.debug(f"Writing output to: {output}")
    output.write_text(content)


def _process_single_result(context: TranspilingContext, error_list: list[TranspileError]) -> None:

    output_code: str = context.transpiled_code or ""

    if any(err.kind == ErrorKind.PARSING for err in error_list):
        output_code = context.source_code or ""

    elif context.validator:
        logger.debug(f"Validating transpiled code for file: {context.input_path}")
        validation_result = _validation(context.validator, context.config, str(context.transpiled_code))
        # Potentially expensive, only evaluate if debug is enabled
        if logger.isEnabledFor(logging.DEBUG):
            msg = f"Finished validating transpiled code for file: {context.input_path} (result: {validation_result})"
            logger.debug(msg)
        if validation_result.exception_msg is not None:
            error = TranspileError(
                "VALIDATION_ERROR",
                ErrorKind.VALIDATION,
                ErrorSeverity.WARNING,
                context.input_path,
                validation_result.exception_msg,
            )
            error_list.append(error)
        output_code = validation_result.validated_sql

    output_path = cast(Path, context.output_path)
    with output_path.open("w") as w:
        # The above adds a java-style comment block at the top of the output file
        # This would break .py or .json outputs so we disable it for now.
        # w.write(make_header(context.input_path, error_list))
        w.write(output_code)

    logger.info(f"Processed file: {context.input_path} (errors: {len(error_list)})")


def make_header(file_path: Path, errors: list[TranspileError]) -> str:
    header = ""
    failed_producing_output = False
    diag_by_severity = {
        severity.name: list(diags) for severity, diags in itertools.groupby(errors, key=lambda x: x.severity)
    }
    line_numbers: dict[int, int] = {}

    if ErrorSeverity.ERROR.name in diag_by_severity:
        header += f"/*\n    Failed transpilation of {file_path}\n"
        header += "\n    The following errors were found while transpiling:\n"
        header += _append_diagnostics(diag_by_severity[ErrorSeverity.ERROR.name], line_numbers)
        failed_producing_output = failed_producing_output or any(
            x.kind == ErrorKind.PARSING for x in diag_by_severity[ErrorSeverity.ERROR.name]
        )
    else:
        header += f"/*\n    Successfully transpiled from {file_path}\n"

    if ErrorSeverity.WARNING.name in diag_by_severity:
        header += "\n    The following warnings were found while transpiling:\n"
        header += _append_diagnostics(diag_by_severity[ErrorSeverity.WARNING.name], line_numbers)

    if failed_producing_output:
        header += "\n\n    Parsing errors prevented the converter from translating the input query.\n"
        header += "    We reproduce the input query unchanged below.\n\n"

    header += "*/\n"

    header_line_count = header.count("\n")

    for unshifted in line_numbers:
        line_numbers[unshifted] = header_line_count + unshifted + 1

    return header.format(line_numbers=line_numbers)


def _append_diagnostics(diagnostics: list[TranspileError], line_numbers: dict) -> str:
    header = ""
    grouped_by_message = {msg: list(diags) for msg, diags in itertools.groupby(diagnostics, lambda x: x.message)}
    for msg, occurrences in grouped_by_message.items():
        for occurrence in occurrences:
            if occurrence.range:
                line_numbers.update({occurrence.range.start.line: 0})
        header += _append_diagnostic(msg, occurrences)
    return header


def _append_diagnostic(msg: str, diags: list[TranspileError]) -> str:
    positions = [
        f"[{{line_numbers[{diag.range.start.line}]}}:{diag.range.start.character + 1}]" for diag in diags if diag.range
    ]
    message = msg.replace("{", "{{").replace("}", "}}").replace("/n", "\n        ")
    result = f"      - {message}\n" if len(positions) != 1 else f"      - {positions[0]} {message}\n"
    if len(positions) > 1:
        positions_str = ", ".join(positions)
        result += f"          Occurred {len(diags)} times at the following positions: {positions_str}\n"
    elif len(diags) > 1:
        result += f"          Occurred {len(diags)} times\n"
    return result


async def _process_many_files(
    config: TranspileConfig,
    validator: Validator | None,
    transpiler: TranspileEngine,
    input_path: Path,
    output_folder: Path,
    files: list[Path],
) -> tuple[int, list[TranspileError]]:
    counter = 0
    all_errors: list[TranspileError] = []

    context = TranspilingContext(
        config=config, validator=validator, transpiler=transpiler, input_path=input_path, output_folder=output_folder
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Processing next {len(files)} files: {files}")
    for file in files:
        if file.name.startswith("."):
            logger.debug(f"Ignored invisible file: {file}")
            continue
        if not transpiler.is_supported_file(file):
            logger.debug(f"Ignored file: {file}")
            continue
        context = dataclasses.replace(context, input_path=file, output_path=output_folder / file.name)
        success_count, error_list = await _process_one_file(context)
        counter = counter + success_count
        all_errors.extend(error_list)
    return counter, all_errors


async def _process_input_dir(config: TranspileConfig, validator: Validator | None, transpiler: TranspileEngine):
    error_list = []
    file_list = []
    counter = 0
    input_path = config.input_path
    output_folder = config.output_path
    if output_folder is None:
        output_folder = input_path.parent / "transpiled"
    make_dir(output_folder)
    for source_dir, _, files in dir_walk(input_path):
        relative_path = cast(Path, source_dir).relative_to(input_path)
        transpiled_dir = output_folder / relative_path
        logger.debug(f"Transpiling files from folder: {source_dir} -> {transpiled_dir}")
        file_list.extend(files)
        no_of_sqls, errors = await _process_many_files(config, validator, transpiler, input_path, transpiled_dir, files)
        counter = counter + no_of_sqls
        error_list.extend(errors)
    return TranspileStatus(file_list, counter, error_list)


async def _process_input_file(
    config: TranspileConfig, validator: Validator | None, transpiler: TranspileEngine
) -> TranspileStatus:
    if not transpiler.is_supported_file(config.input_path):
        msg = f"{config.input_source} is not a supported file."
        logger.warning(msg)
        # silently ignore non-sql files
        return TranspileStatus([], 0, [])
    msg = f"Transpiling file: {config.input_path!s}"
    logger.info(msg)
    output_folder = config.output_path
    if output_folder is None:
        output_folder = config.input_path.parent / "transpiled"
    make_dir(output_folder)
    output_file = output_folder / config.input_path.name
    context = TranspilingContext(
        config=config,
        validator=validator,
        transpiler=transpiler,
        input_path=config.input_path,
        output_folder=output_folder,
        output_path=output_file,
    )
    no_of_sqls, error_list = await _process_one_file(context)
    return TranspileStatus([config.input_path], no_of_sqls, error_list)


async def transpile(
    workspace_client: WorkspaceClient, engine: TranspileEngine, config: TranspileConfig
) -> tuple[JsonObject, list[TranspileError]]:
    await engine.initialize(config)
    status, errors = await _do_transpile(workspace_client, engine, config)
    await engine.shutdown()
    logger.info("Done transpiling.")
    return status, errors


async def _do_transpile(
    workspace_client: WorkspaceClient, engine: TranspileEngine, config: TranspileConfig
) -> tuple[JsonObject, list[TranspileError]]:
    """
    [Experimental] Transpiles the SQL queries from one dialect to another.

    :param workspace_client: The WorkspaceClient object.
    :param engine: The TranspileEngine.
    :param config: The configuration for the morph operation.
    """
    if not config.input_source:
        logger.error("Input SQL path is not provided.")
        raise ValueError("Input SQL path is not provided.")

    validator = None
    if not config.skip_validation:
        sql_backend = db_sql.get_sql_backend(workspace_client)
        logger.info(f"SQL Backend used for query validation: {type(sql_backend).__name__}")
        validator = Validator(sql_backend)
    if config.input_source is None:
        raise ValueError("Missing input source!")
    if config.input_path.is_dir():
        logger.debug(f"Starting to process input directory: {config.input_path}")
        result = await _process_input_dir(config, validator, engine)
    elif config.input_path.is_file():
        logger.debug(f"Starting to process input file: {config.input_path}")
        result = await _process_input_file(config, validator, engine)
    else:
        msg = f"{config.input_source} does not exist."
        logger.error(msg)
        raise FileNotFoundError(msg)
    logger.debug(f"Transpiler results: {result}")

    if not config.skip_validation:
        logger.info(f"SQL validation errors: {result.validation_error_count}")

    # TODO: Refactor this so that errors are written while transpiling instead of waiting until the end.
    if result.error_list and config.error_path is not None:
        with config.error_path.open("a", encoding="utf-8") as e:
            e.writelines(f"{err}\n" for err in result.error_list)
        error_log_file = str(config.error_path)
    else:
        error_log_file = None

    status = {
        "total_files_processed": len(result.file_list),
        "total_queries_processed": result.no_of_transpiled_queries,
        "analysis_error_count": result.analysis_error_count,
        "parsing_error_count": result.parsing_error_count,
        "validation_error_count": result.validation_error_count,
        "generation_error_count": result.generation_error_count,
        "error_log_file": error_log_file,
    }
    logger.debug(f"Transpiler Status: {status}")
    return status, result.error_list


def verify_workspace_client(workspace_client: WorkspaceClient) -> WorkspaceClient:
    """
    [Private] Verifies and updates the workspace client configuration.

    TODO: In future refactor this function so it can be used for reconcile module without cross access.
    """

    # Using reflection to set right value for _product_info for telemetry
    product_info = getattr(workspace_client.config, '_product_info', (None, None))
    if product_info[0] != "lakebridge":
        setattr(workspace_client.config, '_product_info', ('lakebridge', __version__))

    return workspace_client


async def _transpile(
    engine: TranspileEngine, from_dialect: str, to_dialect: str, source_code: str, input_path: Path
) -> TranspileResult:
    return await engine.transpile(from_dialect, to_dialect, source_code, input_path)


def _validation(
    validator: Validator,
    config: TranspileConfig,
    sql: str,
) -> ValidationResult:
    return validator.validate_format_result(config, sql)


@timeit
def transpile_sql(
    workspace_client: WorkspaceClient,
    config: TranspileConfig,
    source_sql: str,
) -> tuple[TranspileResult, ValidationResult | None]:
    """[Experimental] Transpile a single SQL query from one dialect to another."""
    ws_client: WorkspaceClient = verify_workspace_client(workspace_client)

    engine: TranspileEngine = SqlglotEngine()

    transpiler_result = asyncio.run(
        _transpile(engine, cast(str, config.source_dialect), config.target_dialect, source_sql, Path("inline_sql"))
    )

    if config.skip_validation:
        return transpiler_result, None

    sql_backend = db_sql.get_sql_backend(ws_client)
    logger.info(f"SQL Backend used for query validation: {type(sql_backend).__name__}")
    validator = Validator(sql_backend)
    return transpiler_result, _validation(validator, config, transpiler_result.transpiled_code)


@timeit
def transpile_column_exp(
    workspace_client: WorkspaceClient,
    config: TranspileConfig,
    expressions: list[str],
) -> list[tuple[TranspileResult, ValidationResult | None]]:
    """[Experimental] Transpile a list of SQL expressions from one dialect to another."""
    config.skip_validation = True
    result = []
    for sql in expressions:
        result.append(transpile_sql(workspace_client, config, sql))
    return result
