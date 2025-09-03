import logging
from pathlib import Path


from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.install import WheelInstaller
from databricks.labs.lakebridge.transpiler.execute import transpile
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository
from .common_utils import run_transpile_and_assert

logger = logging.getLogger(__name__)


def _install_bladebridge(transpiler_repository: TranspilerRepository, bladebridge_artifact: Path | None) -> tuple:
    WheelInstaller(transpiler_repository, "bladebridge", "databricks-bb-plugin", bladebridge_artifact).install()
    config_path = transpiler_repository.transpiler_config_path("Bladebridge")
    return config_path, LSPEngine.from_config_path(config_path)


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

    config_path, lsp_engine = _install_bladebridge(transpiler_repository, bladebridge_artifact)
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


async def test_transpile_sql_file(ws: WorkspaceClient, tmp_path: Path) -> None:
    labs_path = tmp_path / "labs"
    output_folder = tmp_path / "output"
    transpiler_repository = TranspilerRepository(labs_path)
    await _transpile_bb_sql_file(ws, transpiler_repository, output_folder)


async def _transpile_bb_sql_file(
    ws: WorkspaceClient,
    transpiler_repository: TranspilerRepository,
    bb_output_folder: Path,
) -> None:
    # SQL Version installs latest Bladebridge from pypi
    config_path, lsp_engine = _install_bladebridge(transpiler_repository, None)
    bb_input_source = Path(__file__).parent.parent.parent / "resources" / "functional" / "teradata" / "integration"
    # The expected SQL Block is custom formatted to match the output of Bladebridge exactly.
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
    # The expected SQL Block is custom formatted to match the output of Bladebridge exactly.
    # TODO Validate why Morpheus and Bladebridge provides different output for same input, Bladebridge seems to be more correct on validation focused errors.
    expected_validation_failure_sql = """-------------- Exception Start-------------------
/*
[UNRESOLVED_ROUTINE] Cannot resolve routine `cole` on search path [`system`.`builtin`, `system`.`session`, `catalog`.`schema`].
*/
select cole(hello) world from table;

 ---------------Exception End --------------------"""

    await run_transpile_and_assert(
        ws,
        lsp_engine,
        config_path,
        bb_input_source,
        bb_output_folder,
        "teradata",
        expected_teradata_sql,
        expected_validation_failure_sql,
    )
