# pylint: disable=all
import collections
import dataclasses
import json
import logging
import os
import subprocess
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO, List

import sqlglot
from sqlglot.expressions import Expression
from sqlglot.dialects.dialect import Dialect
from sqlglot.dialects.databricks import Databricks
from sqlglot.errors import ErrorLevel

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ReportEntry:
    project: str
    commit_hash: str | None
    version: str
    timestamp: str
    source_dialect: str
    target_dialect: str
    file: str
    parsed: int = 0  # 1 for success, 0 for failure
    statements: int = 0  # number of statements parsed
    transpiled: int = 0  # 1 for success, 0 for failure
    transpiled_statements: int = 0  # number of statements transpiled
    failures: List[dict] = dataclasses.field(default_factory=lambda: [])


def sqlglot_run_coverage(dialect, subfolder):
    input_dir = get_env_var("INPUT_DIR_PARENT", required=True)
    output_dir = get_env_var("OUTPUT_DIR", required=True)
    sqlglot_version = sqlglot.__version__
    SQLGLOT_COMMIT_HASH = ""  # C0103 pylint

    if not input_dir:
        raise ValueError("Environment variable `INPUT_DIR_PARENT` is required")
    if not output_dir:
        raise ValueError("Environment variable `OUTPUT_DIR` is required")

    collect_transpilation_stats(
        "SQLGlot",
        SQLGLOT_COMMIT_HASH,
        sqlglot_version,
        dialect,
        Databricks,
        Path(input_dir) / subfolder,
        Path(output_dir),
    )


def local_report(output_dir: Path):
    all = collections.defaultdict(list)
    for file in output_dir.rglob("*.json"):
        with file.open("r", encoding="utf8") as f:
            for line in f:
                raw = json.loads(line)
                entry = ReportEntry(**raw)
                all[(entry.project, entry.source_dialect)].append(entry)
    for (project, dialect), entries in sorted(all.items()):
        total = len(entries)
        parsed = sum(entry.parsed for entry in entries)
        transpiled = sum(entry.transpiled for entry in entries)
        parse_ratio = parsed / total
        transpile_ratio = transpiled / total
        print(
            f"{project} -> {dialect}: {parse_ratio:.2%} parsed ({parsed}/{total}), "
            f"{transpile_ratio:.2%} transpiled ({transpiled}/{total})"
        )


def get_supported_sql_files(input_dir: Path) -> Generator[Path, None, None]:
    yield from filter(lambda item: item.is_file() and item.suffix.lower() in [".sql", ".ddl"], input_dir.rglob("*"))


def write_json_line(file: TextIO, content: ReportEntry):
    json.dump(dataclasses.asdict(content), file)
    file.write("\n")


def get_env_var(env_var: str, *, required: bool = False) -> str | None:
    """
    Get the value of an environment variable.

    :param env_var: The name of the environment variable to get the value of.
    :param required: Indicates if the environment variable is required and raises a ValueError if it's not set.
    :return: Returns the environment variable's value, or None if it's not set and not required.
    """
    value = os.getenv(env_var)
    if value is None and required:
        message = f"Environment variable {env_var} is not set"
        raise ValueError(message)
    return value


def get_current_commit_hash() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parent,
            )
            .decode("ascii")
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Could not get the current commit hash. {e!s}")
        return None


def get_current_time_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_sql(sql: str, dialect: type[Dialect]) -> list[Expression]:
    return [
        expression for expression in sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.IMMEDIATE) if expression
    ]


def generate_sql(expressions: list[Expression], dialect: type[Dialect]) -> list[str]:
    generator_dialect = Dialect.get_or_raise(dialect)
    return [generator_dialect.generate(expression, copy=False) for expression in expressions if expression]


def _ensure_valid_io_paths(input_dir: Path, result_dir: Path):
    if not input_dir.exists() or not input_dir.is_dir():
        message = f"The input path {input_dir} doesn't exist or is not a directory"
        raise NotADirectoryError(message)

    if not result_dir.exists():
        logger.info(f"Creating the output directory {result_dir}")
        result_dir.mkdir(parents=True)
    elif not result_dir.is_dir():
        message = f"The output path {result_dir} exists but is not a directory"
        raise NotADirectoryError(message)


def _get_report_file_path(
    project: str,
    source_dialect: type[Dialect],
    target_dialect: type[Dialect],
    result_dir: Path,
) -> Path:
    source_dialect_name = source_dialect.__name__
    target_dialect_name = target_dialect.__name__
    current_time_ns = time.time_ns()
    return result_dir / f"{project}_{source_dialect_name}_{target_dialect_name}_{current_time_ns}.json".lower()


def _prepare_report_entry(
    project: str,
    commit_hash: str,
    version: str,
    source_dialect: type[Dialect],
    target_dialect: type[Dialect],
    file_path: str,
    sql: str,
) -> ReportEntry:
    report_entry = ReportEntry(
        project=project,
        commit_hash=commit_hash,
        version=version,
        timestamp=get_current_time_utc().isoformat(),
        source_dialect=source_dialect.__name__,
        target_dialect=target_dialect.__name__,
        file=file_path,
    )
    try:
        expressions = parse_sql(sql, source_dialect)
        report_entry.parsed = 1
        report_entry.statements = len(expressions)
    except Exception as pe:
        report_entry.failures.append({'error_code': type(pe).__name__, 'error_message': repr(pe)})
        return report_entry

    try:
        generated_sqls = generate_sql(expressions, target_dialect)
        report_entry.transpiled = 1
        report_entry.transpiled_statements = len([sql for sql in generated_sqls if sql.strip()])
    except Exception as te:
        report_entry.failures.append({'error_code': type(te).__name__, 'error_message': repr(te)})

    return report_entry


def collect_transpilation_stats(
    project: str,
    commit_hash: str,
    version: str,
    source_dialect: type[Dialect],
    target_dialect: type[Dialect],
    input_dir: Path,
    result_dir: Path,
):
    _ensure_valid_io_paths(input_dir, result_dir)
    report_file_path = _get_report_file_path(project, source_dialect, target_dialect, result_dir)

    with report_file_path.open("w", encoding="utf8") as report_file:
        for input_file in get_supported_sql_files(input_dir):
            with input_file.open("r", encoding="utf-8-sig") as file:
                sql = file.read()

            file_path = str(input_file.absolute().relative_to(input_dir.parent.absolute()))
            report_entry = _prepare_report_entry(
                project,
                commit_hash,
                version,
                source_dialect,
                target_dialect,
                file_path,
                sql,
            )
            write_json_line(report_file, report_entry)
