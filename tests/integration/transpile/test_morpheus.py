from pathlib import Path

from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.install import MavenInstaller
from databricks.labs.lakebridge.transpiler.execute import transpile
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


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
    MavenInstaller(transpiler_repository, "morpheus", "com.databricks.labs", "databricks-morph-plugin").install()
    config_path = transpiler_repository.transpiler_config_path("Morpheus")
    lsp_engine = LSPEngine.from_config_path(config_path)
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "dbt"

    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="snowflake",
        input_source=str(input_source),
        output_folder=str(output_folder),
        skip_validation=False,
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
