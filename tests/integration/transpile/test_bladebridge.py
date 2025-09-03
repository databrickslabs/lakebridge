import logging
from pathlib import Path


from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.install import WheelInstaller
from databricks.labs.lakebridge.transpiler.execute import transpile
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository

logger = logging.getLogger(__name__)


async def test_transpiles_informatica_with_sparksql(
    ws: WorkspaceClient,
    bladebridge_artifact: Path,
    tmp_path: Path,
) -> None:
    labs_path = tmp_path / "labs"
    output_folder = tmp_path / "output"
    transpiler_repository = TranspilerRepository(labs_path)
    await _transpile_informatica_with_sparksql(ws, transpiler_repository, bladebridge_artifact, output_folder)


async def _transpile_informatica_with_sparksql(
    ws: WorkspaceClient,
    transpiler_repository: TranspilerRepository,
    bladebridge_artifact: Path,
    output_folder: Path,
) -> None:
    WheelInstaller(transpiler_repository, "bladebridge", "databricks-bb-plugin", bladebridge_artifact).install()
    config_path = transpiler_repository.transpiler_config_path("Bladebridge")
    lsp_engine = LSPEngine.from_config_path(config_path)
    input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "informatica"
    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect="informatica (desktop edition)",
        input_source=str(input_source),
        output_folder=str(output_folder),
        skip_validation=True,
        catalog_name="catalog",
        schema_name="schema",
        transpiler_options={"target-tech": "SPARKSQL"},
    )
    # TODO: Load the engine here, via the validation path.
    await transpile(ws, lsp_engine, transpile_config)
    # TODO: This seems to be flaky; debug logging to help diagnose the flakiness.
    files = [f.name for f in output_folder.iterdir()]
    logger.debug(f"Transpiled files: {files}")
    assert (output_folder / "m_employees_load.py").exists()
    assert (output_folder / "wf_m_employees_load.json").exists()
    assert (output_folder / "wf_m_employees_load_params.py").exists()
