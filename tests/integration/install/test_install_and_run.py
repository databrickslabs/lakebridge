import asyncio
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from databricks.labs.lakebridge.config import TranspileConfig, TranspileResult
from databricks.labs.lakebridge.install import TranspilerInstaller
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPEngine


def format_transpiled(sql: str) -> str:
    parts = sql.lower().split("\n")
    stripped = [s.strip() for s in parts]
    sql = " ".join(stripped)
    return sql


async def run_lsp_operations_sync(
    lsp_engine: LSPEngine, transpile_config: TranspileConfig, input_source: str, sql_code: str
) -> TranspileResult:
    """Helper function to run LSP operations synchronously"""
    await lsp_engine.initialize(transpile_config)
    dialect = transpile_config.source_dialect or ""  # Ensure it's a string
    input_file = Path(input_source) / "some_query.sql"
    result = await lsp_engine.transpile(dialect, "databricks", sql_code, input_file)
    await lsp_engine.shutdown()
    return result


def test_installs_and_runs_local_bladebridge(bladebridge_artifact: Path) -> None:
    with TemporaryDirectory() as tmpdir:
        with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
            _install_and_run_local_bladebridge(bladebridge_artifact)


def _install_and_run_local_bladebridge(bladebridge_artifact: Path) -> None:
    bladebridge = TranspilerInstaller.transpilers_path() / "bladebridge"
    assert not bladebridge.exists()
    TranspilerInstaller.install_from_pypi("bladebridge", "databricks-bb-plugin", bladebridge_artifact)
    config_path = bladebridge / "lib" / "config.yml"
    assert config_path.exists()
    version_path = bladebridge / "state" / "version.json"
    assert version_path.exists()
    lsp_engine = LSPEngine.from_config_path(config_path)
    with TemporaryDirectory() as input_source:
        with TemporaryDirectory() as output_folder:
            transpile_config = TranspileConfig(
                transpiler_config_path=str(config_path),
                source_dialect="oracle",
                input_source=input_source,
                output_folder=output_folder,
                sdk_config={"cluster_id": "test_cluster"},
                skip_validation=False,
                catalog_name="catalog",
                schema_name="schema",
            )

            sql_code = "select * from employees"
            result = asyncio.run(run_lsp_operations_sync(lsp_engine, transpile_config, input_source, sql_code))
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code


def test_installs_and_runs_pypi_bladebridge():
    if sys.platform == "win32":
        _install_and_run_pypi_bladebridge()
    else:
        with TemporaryDirectory() as tmpdir:
            with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
                _install_and_run_pypi_bladebridge()


def _install_and_run_pypi_bladebridge() -> None:
    bladebridge = TranspilerInstaller.transpilers_path() / "bladebridge"
    if sys.platform == "win32" and bladebridge.exists():
        shutil.rmtree(bladebridge)
    assert not bladebridge.exists()
    TranspilerInstaller.install_from_pypi("bladebridge", "databricks-bb-plugin")
    config_path = bladebridge / "lib" / "config.yml"
    assert config_path.exists()
    version_path = bladebridge / "state" / "version.json"
    assert version_path.exists()
    lsp_engine = LSPEngine.from_config_path(config_path)
    with TemporaryDirectory() as input_source:
        with TemporaryDirectory() as output_folder:
            transpile_config = TranspileConfig(
                transpiler_config_path=str(config_path),
                source_dialect="oracle",
                input_source=input_source,
                output_folder=output_folder,
                sdk_config={"cluster_id": "test_cluster"},
                skip_validation=False,
                catalog_name="catalog",
                schema_name="schema",
            )

            sql_code = "select * from employees"
            result = asyncio.run(run_lsp_operations_sync(lsp_engine, transpile_config, input_source, sql_code))
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code


def test_installs_and_runs_local_morpheus(morpheus_artifact):
    # if sys.platform == "win32":
    #    _install_and_run_pypi_bladebridge()
    # else:
    with TemporaryDirectory() as tmpdir:
        with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
            _install_and_run_local_morpheus(morpheus_artifact)


def _install_and_run_local_morpheus(morpheus_artifact):
    morpheus = TranspilerInstaller.transpilers_path() / "morpheus"
    assert not morpheus.exists()
    TranspilerInstaller.install_from_maven(
        "morpheus", "com.databricks.labs", "databricks-morph-plugin", morpheus_artifact
    )
    morpheus = TranspilerInstaller.transpilers_path() / "morpheus"
    config_path = morpheus / "lib" / "config.yml"
    assert config_path.exists()
    main_path = morpheus / "lib" / "databricks-morph-plugin.jar"
    assert main_path.exists()
    version_path = morpheus / "state" / "version.json"
    assert version_path.exists()
    lsp_engine = LSPEngine.from_config_path(config_path)
    with TemporaryDirectory() as input_source:
        with TemporaryDirectory() as output_folder:
            transpile_config = TranspileConfig(
                transpiler_config_path=str(config_path),
                source_dialect="snowflake",
                input_source=input_source,
                output_folder=output_folder,
                sdk_config={"cluster_id": "test_cluster"},
                skip_validation=False,
                catalog_name="catalog",
                schema_name="schema",
            )

            sql_code = "select * from employees;"
            result = asyncio.run(run_lsp_operations_sync(lsp_engine, transpile_config, input_source, sql_code))
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code
