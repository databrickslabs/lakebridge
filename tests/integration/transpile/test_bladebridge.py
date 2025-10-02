import json
import logging
from collections.abc import Generator
from functools import cached_property
from pathlib import Path

import pytest
from databricks.labs.blueprint.wheels import ProductInfo
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
    ctx.installation.remove()


def test_transpiles_informatica_to_sparksql(
    application_ctx: ApplicationContext, repository_with_bladebridge: TranspilerRepository, tmp_path: Path, capsys
) -> None:
    """Check that 'transpile' can convert an Informatica (ETL) mapping to SparkSQL using Bladebridge."""
    # Prepare the application context with a configuration for converting Informatica (ETL)
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "informatica"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    errors_path = output_folder / "errors.log"
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


def test_transpile_teradata_sql(
    application_ctx: ApplicationContext, repository_with_bladebridge: TranspilerRepository, tmp_path: Path, capsys
) -> None:
    """Check that 'transpile' can convert a Teradata (SQL) to DBSQL using Bladebridge, and then validate the output."""
    # Prepare the application context with a configuration for converting Teradata (SQL)
    config_path = repository_with_bladebridge.transpiler_config_path("Bladebridge")
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "teradata" / "integration"
    output_folder = tmp_path / "output"
    output_folder.mkdir(parents=True, exist_ok=True)
    errors_path = output_folder / "errors.log"
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
    cli.transpile(w=application_ctx.workspace_client, ctx=application_ctx)
    (out, _) = capsys.readouterr()

    # Check the conversion summary.
    summary = json.loads(out)
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
