from pathlib import Path

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.execute import transpile
from databricks.labs.lakebridge.transpiler.installers import MavenInstaller
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from .common_utils import run_transpile_and_assert


def _install_morpheus(transpiler_repository: TranspilerRepository) -> tuple:
    MavenInstaller(transpiler_repository, "morpheus", "com.databricks.labs", "databricks-morph-plugin").install()
    config_path = transpiler_repository.transpiler_config_path("Morpheus")
    return config_path, LSPEngine.from_config_path(config_path)


async def test_transpiles_all_dbt_project_files(ws: WorkspaceClient, tmp_path: Path) -> None:
    labs_path = tmp_path / "labs"
    output_folder = tmp_path / "output"
    transpiler_repository = TranspilerRepository(labs_path)
    await _transpile_all_dbt_project_files(ws, transpiler_repository, output_folder)


async def _transpile_all_dbt_project_files(
    ws: WorkspaceClient,
    transpiler_repository: TranspilerRepository,
    output_folder: Path,
) -> None:
    config_path, lsp_engine = _install_morpheus(transpiler_repository)
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "dbt"

    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="snowflake",
        input_source=str(input_source),
        output_folder=str(output_folder),
        skip_validation=True,
        catalog_name="catalog",
        schema_name="schema",
    )
    # TODO: Load the engine here, via the validation path.
    await transpile(ws, lsp_engine, transpile_config)
    assert (output_folder / "top-query.sql").exists()
    assert (output_folder / "dbt_project.yml").exists()
    assert (output_folder / "sub" / "sub-query.sql").exists()
    assert (output_folder / "sub" / "sub-query-bom.sql").exists()
    assert (output_folder / "sub" / "dbt_project.yml").exists()


async def test_transpile_sql_file(ws: WorkspaceClient, tmp_path: Path) -> None:
    labs_path = tmp_path / "labs"
    output_folder = tmp_path / "output"
    transpiler_repository = TranspilerRepository(labs_path)
    await _transpile_sql_file(ws, transpiler_repository, output_folder)


async def _transpile_sql_file(
    ws: WorkspaceClient,
    transpiler_repository: TranspilerRepository,
    output_folder: Path,
) -> None:
    config_path, lsp_engine = _install_morpheus(transpiler_repository)
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "snowflake" / "integration"
    # The expected SQL Block is custom formatted to match the output of Morpheus exactly.
    expected_sql = """CREATE TABLE employee (
  employee_id DECIMAL(38, 0),
  first_name VARCHAR(50) NOT NULL,
  last_name VARCHAR(50) NOT NULL,
  birth_date DATE,
  hire_date DATE,
  salary DECIMAL(10, 2),
  department_id DECIMAL(38, 0),
  remarks VARIANT
);"""
    # The expected SQL Block is custom formatted to match the output of Morpheus exactly.
    expected_failure_sql = """-------------- Exception Start-------------------
/*
[UNRESOLVED_ROUTINE] Cannot resolve routine `COLE` on search path [`system`.`builtin`, `system`.`session`, `catalog`.`schema`].
*/
SELECT
  COLE(hello) AS world
FROM
  table;
 ---------------Exception End --------------------"""

    # TODO: Load the engine here, via the validation path.
    await run_transpile_and_assert(
        ws,
        lsp_engine,
        config_path,
        input_source,
        output_folder,
        "snowflake",
        expected_sql,
        expected_failure_sql,
    )
