import contextlib
import json
import logging
from collections.abc import Generator
from functools import cached_property
from pathlib import Path

import pytest
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.labs.blueprint.paths import WorkspacePath
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge import cli
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.contexts.application import ApplicationContext
from databricks.labs.lakebridge.transpiler.installers import WheelInstaller
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from .common_utils import assert_sql_outputs

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def repository_with_bladebridge(tmp_path_factory) -> TranspilerRepository:
    """A module-scoped repository with the latest published version of Bladebridge installed, for re-use across tests."""
    labs_path = tmp_path_factory.mktemp("labs")
    transpiler_repository = TranspilerRepository(labs_path)
    path = WheelInstaller(transpiler_repository, "bladebridge", "databricks-bb-plugin").install()
    assert path is not None and path.exists()
    return transpiler_repository


@contextlib.contextmanager
def capture_bladebridge_logs(
    transpiler_repository: TranspilerRepository,
    *,
    level: int = logging.DEBUG,
) -> Generator[None, None, None]:
    """Reset the logs from Bladebridge before yielding, and capture them afterward, to help with test debugging."""
    # TODO: Move this into the core?
    #   - Extend the LSP config.yml to describe where error logs go.
    #   - If the LSP server fails, capture the error logs automatically.

    # Step 1: Remove any existing log files, so we know that anything afterward is fresh.
    bladebridge_lib_dir = transpiler_repository.transpilers_path() / "bladebridge" / "lib"
    for log_file in bladebridge_lib_dir.glob("*.log"):
        logger.debug(f"Removing existing log file: {log_file}")
        log_file.unlink(missing_ok=True)

    # Step 2: Yield to the caller, who will presumably run some Bladebridge operations.
    yield

    # Step 3: Capture any logs that were produced, to help with debugging if the test failed.
    produced_log_files = list(bladebridge_lib_dir.glob("*.log"))
    logger.debug(f"Captured {len(produced_log_files)} log file(s): {produced_log_files}")
    if not logger.isEnabledFor(level):
        return
    for log_file in produced_log_files:
        logger.log(level, f"============ Bladebridge log: {log_file.name} starting... ==================")
        for line in log_file.open(encoding="utf-8", errors="replace"):
            logger.log(level, f"{log_file.name}: {line.strip()}")
        logger.log(level, f"============ Bladebridge log: {log_file.name} finished. ====================")


class MockApplicationContext(ApplicationContext):
    """A mock application context that uses a unique installation path."""

    @cached_property
    def product_info(self) -> ProductInfo:
        return ProductInfo.for_testing(ApplicationContext)


@pytest.fixture
def application_ctx(ws: WorkspaceClient) -> Generator[ApplicationContext, None, None]:
    """A mock application context with a unique installation path, cleaned up after the test."""
    ctx = MockApplicationContext(ws)
    yield ctx
    if WorkspacePath(ws, ctx.installation.install_folder()).exists():
        ctx.installation.remove()


@pytest.fixture(name="errors_path")
def capture_errors_log(tmp_path: Path) -> Generator[Path, None, None]:
    """The path to an errors log file. If it exists after the test, its content will be logged to help with debugging."""
    path = tmp_path / "errors.log"
    yield path
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            errors_logged = list(f)
    except OSError:
        logger.debug("No errors log found.")
    else:
        for line in errors_logged:
            logger.error(f"Error logged: {line.strip()}")


def test_transpiles_informatica_to_sparksql(
    application_ctx: ApplicationContext,
    repository_with_bladebridge: TranspilerRepository,
    errors_path: Path,
    tmp_path: Path,
    capsys,
) -> None:
    """Check that 'transpile' can convert an Informatica (ETL) mapping to SparkSQL using Bladebridge."""
    # Prepare the application context with a configuration for converting Informatica (ETL)
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "informatica"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="informatica (desktop edition)",
        input_source=str(input_source),
        output_folder=str(output_folder),
        error_file_path=str(errors_path),
        skip_validation=True,
        transpiler_options={"overrides-file": None, "target-tech": "SPARKSQL"},
    )
    application_ctx.installation.save(transpile_config)

    # Run the conversion.
    with capture_bladebridge_logs(repository_with_bladebridge):
        cli.transpile(
            w=application_ctx.workspace_client,
            ctx=application_ctx,
            transpiler_repository=repository_with_bladebridge,
        )
    (out, _) = capsys.readouterr()

    # Check the conversion summary.
    summary = json.loads(out)
    assert summary == [
        {
            "total_files_processed": 1,
            "total_queries_processed": 1,
            "analysis_error_count": 0,
            "parsing_error_count": 0,
            "validation_error_count": 0,
            "generation_error_count": 0,
            "error_log_file": None,
        }
    ]

    # Check the conversion by merely looking for the files we expect from our reference Informatica mapping.
    assert (output_folder / "m_employees_load.py").exists()
    assert (output_folder / "wf_m_employees_load.json").exists()
    assert (output_folder / "wf_m_employees_load_params.py").exists()
    # No errors should have been logged, which means the errors file should not exist.
    assert not errors_path.exists()


@pytest.mark.parametrize("provide_overrides", [True, False])
def test_transpiles_informatica_to_sparksql_non_interactive(
    provide_overrides: bool,
    application_ctx: ApplicationContext,
    repository_with_bladebridge: TranspilerRepository,
    errors_path: Path,
    tmp_path: Path,
    capsys,
) -> None:
    """Check that 'transpile' can non-interactively convert an Informatica (ETL) mapping to SparkSQL using Bladebridge."""
    # Prepare the application context as if it were non-interactive (no config.yml file).
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "informatica"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, str] = {}
    if provide_overrides:
        # This is horrible but we need it for the minimum valid overrides file that will work with Informatica/SparkSQL.
        transpilers_path = repository_with_bladebridge.transpilers_path()
        overrides_base = next(transpilers_path.glob("**/base_infapc2databricks_sparksql.json"))
        overrides_file = tmp_path / "overrides.json"
        overrides_file.write_text(json.dumps({"inherit_from": [str(overrides_base.absolute())]}), encoding="utf-8")
        kwargs["overrides_file"] = str(overrides_file)

    # Run the conversion: everything has to be passed as parameters.
    with capture_bladebridge_logs(repository_with_bladebridge):
        cli.transpile(
            w=application_ctx.workspace_client,
            transpiler_config_path=str(config_path),
            source_dialect="informatica (desktop edition)",
            target_technology="SPARKSQL",
            input_source=str(input_source),
            output_folder=str(output_folder),
            error_file_path=str(errors_path),
            ctx=application_ctx,
            transpiler_repository=repository_with_bladebridge,
            **kwargs,
        )
    (out, _) = capsys.readouterr()

    _check_transpile_informatica_to_sparksql(out, output_folder, errors_path)


def _check_transpile_informatica_to_sparksql(stdout: str, output_folder: Path, errors_path: Path) -> None:
    # Check the conversion summary.
    summary = json.loads(stdout)
    assert summary == [
        {
            "total_files_processed": 1,
            "total_queries_processed": 1,
            "analysis_error_count": 0,
            "parsing_error_count": 0,
            "validation_error_count": 0,
            "generation_error_count": 0,
            "error_log_file": None,
        }
    ]

    # Check the conversion by merely looking for the files we expect from our reference Informatica mapping.
    assert (output_folder / "m_employees_load.py").exists()
    assert (output_folder / "wf_m_employees_load.json").exists()
    assert (output_folder / "wf_m_employees_load_params.py").exists()
    # No errors should have been logged, which means the errors file should not exist.
    assert not errors_path.exists()


def test_transpile_teradata_sql(
    application_ctx: ApplicationContext,
    repository_with_bladebridge: TranspilerRepository,
    errors_path: Path,
    tmp_path: Path,
    capsys,
) -> None:
    """Check that 'transpile' can convert a Teradata (SQL) to DBSQL using Bladebridge, and then validate the output."""
    # Prepare the application context with a configuration for converting Teradata (SQL)
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "teradata" / "integration"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="teradata",
        input_source=str(input_source),
        output_folder=str(output_folder),
        error_file_path=str(errors_path),
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
        transpiler_options={"overrides-file": None},
    )
    application_ctx.installation.save(transpile_config)

    # Run the conversion.
    with capture_bladebridge_logs(repository_with_bladebridge):
        cli.transpile(w=application_ctx.workspace_client, ctx=application_ctx)
    (out, _) = capsys.readouterr()

    _check_transpile_teradata_sql(out, output_folder, errors_path)


@pytest.mark.parametrize("provide_overrides", [True, False])
def test_transpile_teradata_sql_non_interactive(
    provide_overrides: bool,
    application_ctx: ApplicationContext,
    repository_with_bladebridge: TranspilerRepository,
    errors_path: Path,
    tmp_path: Path,
    capsys,
) -> None:
    """Check that 'transpile' can non-interactively convert a Teradata (SQL) to DBSQL using Bladebridge, and then validate the output."""
    # Prepare the application context as if it were non-interactive (no config.yml file).
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "teradata" / "integration"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, str] = {}
    if provide_overrides:
        # This is horrible but we need it for the minimum valid overrides file that will work with Teradata.
        transpilers_path = repository_with_bladebridge.transpilers_path()
        overrides_base = next(transpilers_path.glob("**/base_teradata2databricks_sql.json"))
        overrides_file = tmp_path / "overrides.json"
        overrides_file.write_text(json.dumps({"inherit_from": [str(overrides_base.absolute())]}), encoding="utf-8")
        kwargs["overrides_file"] = str(overrides_file)

    # Run the conversion: everything has to be passed as parameters.
    with capture_bladebridge_logs(repository_with_bladebridge):
        cli.transpile(
            w=application_ctx.workspace_client,
            transpiler_config_path=str(config_path),
            source_dialect="teradata",
            input_source=str(input_source),
            output_folder=str(output_folder),
            error_file_path=str(errors_path),
            skip_validation="false",
            catalog_name="catalog",
            schema_name="schema",
            ctx=application_ctx,
            transpiler_repository=repository_with_bladebridge,
            **kwargs,
        )
    (out, _) = capsys.readouterr()

    _check_transpile_teradata_sql(out, output_folder, errors_path)


def _check_transpile_teradata_sql(stdout: str, output_folder: Path, errors_path: Path) -> None:
    # Check the conversion summary.
    summary = json.loads(stdout)
    assert summary == [
        {
            "total_files_processed": 2,
            "total_queries_processed": 2,
            "analysis_error_count": 0,
            "parsing_error_count": 0,
            "validation_error_count": 1,
            "generation_error_count": 0,
            "error_log_file": str(errors_path),
        }
    ]

    # Check the output.
    # Note: these are formatted exactly to match the output of Bladebridge.
    expected_teradata_sql = """CREATE TABLE REF_TABLE
(
    col1    TINYINT NOT NULL,
    col2    SMALLINT NOT NULL,
    col3    INTEGER NOT NULL,
    col4    BIGINT NOT NULL,
    col5    DECIMAL(10,2) NOT NULL,
    col6    DECIMAL(18,4) NOT NULL,
    col7    TIMESTAMP NOT NULL,
    col8    TIMESTAMP,
    col9    TIMESTAMP NOT NULL,
    col10   STRING NOT NULL,
    col11   STRING NOT NULL,
    col12   STRING,
    col13   DECIMAL(10,0) NOT NULL,
    col14   DECIMAL(18,6) NOT NULL,
    col15   DECIMAL(18,1) NOT NULL DEFAULT 0.0,
    col16   DATE,
    col17 STRING COLLATE UTF8_LCASE,
    col18   FLOAT NOT NULL,
PRIMARY KEY (col1,col3) )
TBLPROPERTIES('delta.feature.allowColumnDefaults' = 'supported');"""
    expected_validation_failure_sql = """-------------- Exception Start-------------------
/*
[UNRESOLVED_ROUTINE] Cannot resolve routine `cole` on search path [`system`.`builtin`, `system`.`session`, `catalog`.`schema`].
*/
select cole(hello) world from table;

 ---------------Exception End --------------------"""
    assert_sql_outputs(
        output_folder,
        expected_sql=expected_teradata_sql,
        expected_failure_sql=expected_validation_failure_sql,
    )

    # Verify the errors that were reported.
    reported_errors = list(errors_path.open())
    [only_error] = reported_errors
    assert "[UNRESOLVED_ROUTINE] Cannot resolve routine `cole` on search path" in only_error
