import logging
from collections.abc import Generator
from pathlib import Path
from email import policy
from email.message import Message
from email.parser import Parser as EmailParser

import pytest

from databricks.labs.lakebridge.config import TranspileConfig, TranspileResult
from databricks.labs.lakebridge.install import WheelInstaller, MavenInstaller
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine


logger = logging.getLogger(__name__)


def process_email_content(msg: str) -> str | None:
    parser = EmailParser(policy=policy.default)
    message: Message = parser.parsestr(msg)
    result: str | None = None
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() != "multipart":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                assert charset == "utf-8", "Only UTF-8 is supported for now"
                result = (payload.decode(charset) if isinstance(payload, bytes) else str(payload)).rstrip("\n")
                break
    return result


def format_transpiled(sql: str) -> str:
    parts = sql.lower().split("\n")
    stripped = [s.strip() for s in parts]
    sql = " ".join(stripped)
    return sql


def _capture_transpiler_logs(transpiler_repository: TranspilerRepository) -> None:
    """Look for transpiler logs, and replicate them in the test output."""
    logger.debug("Gathering transpiler logs...")
    transpilers_path = transpiler_repository.transpilers_path()
    transpiler_directories = [p for p in transpilers_path.iterdir() if p.is_dir()]
    for transpiler_dir in transpiler_directories:
        log_dir = transpiler_dir / "lib"
        log_files = [log for log in log_dir.glob("*.log") if log.is_file()]
        logger.debug(f"Found {len(log_files)} log files: {log_files}")
        if log_files:
            for log_file in log_files:
                with log_file.open("r", encoding="utf-8") as f:
                    logs = f.read()
                log_name = log_file.relative_to(transpilers_path)
                logger.info(f"Transpiler log for {log_name}:\n[***START OF LOG***]\n{logs}\n[***END OF LOG***]\n")
        else:
            log_dir_name = log_dir.relative_to(transpilers_path)
            logger.info(f"No logs found for transpiler: {log_dir_name}")


@pytest.fixture(name="transpiler_repository")
def log_capturing_transpiler_repository(tmp_path: Path) -> Generator[TranspilerRepository, None, None]:
    labs_path = tmp_path / "labs"
    transpiler_repository = TranspilerRepository(labs_path=labs_path)
    yield transpiler_repository
    # This will run after the test completes, even if it fails.
    _capture_transpiler_logs(transpiler_repository)


async def run_lsp_operations(
    engine: TranspileEngine,
    transpile_config: TranspileConfig,
    input_source: Path,
    sql_code: str,
) -> TranspileResult:
    """Helper function to run LSP operations."""
    await engine.initialize(transpile_config)
    dialect = transpile_config.source_dialect
    assert dialect is not None
    input_file = input_source / "some_query.sql"
    result = await engine.transpile(dialect, "databricks", sql_code, input_file)
    await engine.shutdown()
    return result


# TODO: Remove this test? We really want to test the latest published version.
async def test_installs_and_runs_local_bladebridge(
    bladebridge_artifact: Path,
    transpiler_repository: TranspilerRepository,
    tmp_path: Path,
) -> None:
    input_source = tmp_path / "input_source"
    output_folder = tmp_path / "output_folder"
    location = WheelInstaller(
        transpiler_repository, "bladebridge", "databricks-bb-plugin", bladebridge_artifact
    ).install()
    assert location is not None
    config_path = transpiler_repository.transpiler_config_path("Bladebridge")
    lsp_engine = LSPEngine.from_config_path(config_path)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="oracle",
        input_source=str(input_source),
        output_folder=str(output_folder),
        sdk_config={"cluster_id": "test_cluster"},
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )

    sql_code = "select * from employees"
    result = await run_lsp_operations(lsp_engine, transpile_config, input_source, sql_code)
    transpiled = process_email_content(result.transpiled_code)
    assert transpiled == sql_code


async def test_installs_and_runs_pypi_bladebridge(transpiler_repository: TranspilerRepository, tmp_path: Path) -> None:
    input_source = tmp_path / "input_source"
    output_folder = tmp_path / "output_folder"
    location = WheelInstaller(transpiler_repository, "bladebridge", "databricks-bb-plugin").install()
    assert location is not None
    config_path = transpiler_repository.transpiler_config_path("Bladebridge")
    engine = LSPEngine.from_config_path(config_path)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="oracle",
        input_source=str(input_source),
        output_folder=str(output_folder),
        sdk_config={"cluster_id": "test_cluster"},
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )

    sql_code = "select * from employees"
    result = await run_lsp_operations(engine, transpile_config, input_source, sql_code)
    transpiled = process_email_content(result.transpiled_code)
    assert transpiled == sql_code


# TODO: Remove this test? We really want to test the latest published version.
async def test_installs_and_runs_local_morpheus(
    morpheus_artifact: Path,
    transpiler_repository: TranspilerRepository,
    tmp_path: Path,
) -> None:
    input_source = tmp_path / "input_source"
    output_folder = tmp_path / "output_folder"
    location = MavenInstaller(
        transpiler_repository, "morpheus", "com.databricks.labs", "databricks-morph-plugin", morpheus_artifact
    ).install()
    assert location is not None
    config_path = transpiler_repository.transpiler_config_path("Morpheus")
    engine = LSPEngine.from_config_path(config_path)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="snowflake",
        input_source=str(input_source),
        output_folder=str(output_folder),
        sdk_config={"cluster_id": "test_cluster"},
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )

    sql_code = "select * from employees;"
    result = await run_lsp_operations(engine, transpile_config, input_source, sql_code)
    transpiled = format_transpiled(result.transpiled_code)
    assert transpiled == sql_code


async def test_installs_and_runs_maven_morpheus(transpiler_repository: TranspilerRepository, tmp_path: Path) -> None:
    input_source = tmp_path / "input_source"
    output_folder = tmp_path / "output_folder"
    location = MavenInstaller(
        transpiler_repository, "morpheus", "com.databricks.labs", "databricks-morph-plugin"
    ).install()
    assert location is not None
    config_path = transpiler_repository.transpiler_config_path("Morpheus")
    engine = LSPEngine.from_config_path(config_path)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="snowflake",
        input_source=str(input_source),
        output_folder=str(output_folder),
        sdk_config={"cluster_id": "test_cluster"},
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )

    sql_code = "select * from employees;"
    result = await run_lsp_operations(engine, transpile_config, input_source, sql_code)
    transpiled = format_transpiled(result.transpiled_code)
    assert transpiled == sql_code
