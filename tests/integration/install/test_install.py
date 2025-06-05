import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from databricks.labs.remorph.config import TranspileConfig
from databricks.labs.remorph.install import TranspilerInstaller, MavenInstaller, WorkspaceInstaller, WheelInstaller
from databricks.labs.remorph.transpiler.lsp.lsp_engine import LSPEngine


@pytest.mark.skipif(os.environ.get("CI", "false") == "true", reason="Skipping in CI since we have no installed product")
def test_gets_installed_remorph_version(patched_transpiler_installer):
    version = patched_transpiler_installer.get_installed_version("remorph", False)
    check_valid_version(version)


def test_gets_maven_artifact_version() -> None:
    version = MavenInstaller.get_current_maven_artifact_version("com.databricks", "databricks-connect")
    assert version
    check_valid_version(version)


def test_downloads_from_maven():
    with TemporaryDirectory() as parent:
        path = Path(parent) / "pom.xml"
        success = MavenInstaller.download_artifact_from_maven(
            "com.databricks", "databricks-connect", "16.0.0", path, extension="pom"
        )
        assert success
        assert path.exists()
        assert path.stat().st_size == 5_684


def test_gets_pypi_artifact_version():
    version = WheelInstaller.get_latest_artifact_version_from_pypi("databricks-labs-remorph")
    check_valid_version(version)


def test_downloads_tar_from_pypi():
    with TemporaryDirectory() as parent:
        path = Path(parent) / "archive.tar"
        result = WheelInstaller.download_artifact_from_pypi(
            "databricks-labs-remorph-community-transpiler", "0.0.1", path, extension="tar"
        )
        assert result == 0
        assert path.exists()
        assert path.stat().st_size == 41_656


def test_downloads_whl_from_pypi():
    with TemporaryDirectory() as parent:
        path = Path(parent) / "package.whl"
        result = WheelInstaller.download_artifact_from_pypi(
            "databricks-labs-remorph-community-transpiler", "0.0.1", path
        )
        assert result == 0
        assert path.exists()
        assert path.stat().st_size == 35_270


@pytest.fixture()
def patched_transpiler_installer(tmp_path: Path):
    resources_folder = Path(__file__).parent.parent.parent / "resources" / "transpiler_configs"
    for transpiler in ("bladerunner", "morpheus"):
        target = tmp_path / transpiler
        target.mkdir(exist_ok=True)
        target = target / "lib"
        target.mkdir(exist_ok=True)
        target = target / "config.yml"
        source = resources_folder / transpiler / "lib" / "config.yml"
        shutil.copyfile(source, target)
    with patch.object(TranspilerInstaller, "transpilers_path", return_value=tmp_path):
        yield TranspilerInstaller


def test_lists_all_transpiler_names(patched_transpiler_installer):
    transpiler_names = patched_transpiler_installer.all_transpiler_names()
    assert transpiler_names == {'Morpheus', 'Bladerunner'}


def test_lists_all_dialects(patched_transpiler_installer):
    dialects = patched_transpiler_installer.all_dialects()
    assert dialects == {
        'abinitio',
        'alteryx',
        'athena',
        'datastage',
        'greenplum',
        'hive',
        'impala',
        'informatica (big data edition)',
        'informatica (desktop)',
        'netezza',
        'oozie',
        'oracle',
        'pentaho',
        'pig',
        'presto',
        'redshift',
        'sap hana',
        'sas',
        'snowflake',
        'ssis',
        'synapse',
        'talend',
        'teradata',
        'tsql',
        'vertica',
    }


def test_lists_dialect_transpilers(patched_transpiler_installer):
    transpilers = patched_transpiler_installer.transpilers_with_dialect("snowflake")
    assert transpilers == {'Morpheus', 'Bladerunner'}
    transpilers = patched_transpiler_installer.transpilers_with_dialect("presto")
    assert transpilers == {'Bladerunner'}


def check_valid_version(version: str):
    parts = version.split(".")
    for _, part in enumerate(parts):
        try:
            _ = int(part)
        except ValueError:
            assert False, f"{version} does not look like a valid semver"


def format_transpiled(sql: str) -> str:
    parts = sql.lower().split("\n")
    stripped = [s.strip() for s in parts]
    sql = " ".join(stripped)
    sql = sql.replace(";;", ";")
    return sql


async def test_installs_and_runs_local_rct():
    # Note: This test currently uses the user's home-directory, and doesn't really test the install process if the
    # transpiler is already installed there: many install paths are a no-op if the transpiler is already installed.
    # TODO: Fix to use a temporary location instead of the user's home directory.
    artifact = (
        Path(__file__).parent.parent.parent
        / "resources"
        / "transpiler_configs"
        / "rct"
        / "wheel"
        / "databricks_labs_remorph_community_transpiler-0.0.1-py3-none-any.whl"
    )
    assert artifact.exists()
    TranspilerInstaller.install_from_pypi("rct", "databricks-labs-remorph-community-transpiler", artifact)
    # check file-level installation
    rct = TranspilerInstaller.transpilers_path() / "rct"
    config_path = rct / "lib" / "config.yml"
    assert config_path.exists()
    main_path = rct / "lib" / "main.py"
    assert main_path.exists()
    version_path = rct / "state" / "version.json"
    assert version_path.exists()
    # check execution
    lsp_engine = LSPEngine.from_config_path(config_path)
    with TemporaryDirectory() as input_source:
        with TemporaryDirectory() as output_folder:
            transpile_config = TranspileConfig(
                transpiler_config_path=str(config_path),
                transpiler_options={"-experimental": True},
                source_dialect="snowflake",
                input_source=input_source,
                output_folder=output_folder,
                sdk_config={"cluster_id": "test_cluster"},
                skip_validation=False,
                catalog_name="catalog",
                schema_name="schema",
            )
            await lsp_engine.initialize(transpile_config)
            dialect = transpile_config.source_dialect
            input_file = Path(input_source) / "some_query.sql"
            sql_code = "select * from employees"
            result = await lsp_engine.transpile(dialect, "databricks", sql_code, input_file)
            await lsp_engine.shutdown()
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code


async def test_installs_and_runs_local_bladerunner():
    # Note: This test currently uses the user's home-directory, and doesn't really test the install process if the
    # transpiler is already installed there: many install paths are a no-op if the transpiler is already installed.
    # TODO: Fix to use a temporary location instead of the user's home directory.
    artifact = (
        Path(__file__).parent.parent.parent
        / "resources"
        / "transpiler_configs"
        / "bladerunner"
        / "wheel"
        / "databricks_labs_remorph_bladerunner-0.1.0-py3-none-any.whl"
    )
    assert artifact.exists()
    TranspilerInstaller.install_from_pypi("bladerunner", "databricks-labs-bladerunner", artifact)
    # check file-level installation
    bladerunner = TranspilerInstaller.transpilers_path() / "bladerunner"
    config_path = bladerunner / "lib" / "config.yml"
    assert config_path.exists()
    main_path = bladerunner / "lib" / "main.py"
    assert main_path.exists()
    version_path = bladerunner / "state" / "version.json"
    assert version_path.exists()
    # check execution
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
            await lsp_engine.initialize(transpile_config)
            dialect = transpile_config.source_dialect
            input_file = Path(input_source) / "some_query.sql"
            sql_code = "select * from employees"
            result = await lsp_engine.transpile(dialect, "databricks", sql_code, input_file)
            await lsp_engine.shutdown()
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code


async def test_installs_and_runs_local_morpheus():
    # Note: This test currently uses the user's home-directory, and doesn't really test the install process if the
    # transpiler is already installed there: many install paths are a no-op if the transpiler is already installed.
    # TODO: Fix to use a temporary location instead of the user's home directory.
    artifact = (
        Path(__file__).parent.parent.parent
        / "resources"
        / "transpiler_configs"
        / "morpheus"
        / "jar"
        / "morpheus-lsp-0.2.0-SNAPSHOT-jar-with-dependencies.jar"
    )
    assert artifact.exists()
    TranspilerInstaller.install_from_maven("morpheus", "databricks-labs-remorph", "morpheus-lsp", artifact)
    # check file-level installation
    morpheus = TranspilerInstaller.transpilers_path() / "morpheus"
    config_path = morpheus / "lib" / "config.yml"
    assert config_path.exists()
    main_path = morpheus / "lib" / "morpheus-lsp.jar"
    assert main_path.exists()
    version_path = morpheus / "state" / "version.json"
    assert version_path.exists()
    # check execution
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
            await lsp_engine.initialize(transpile_config)
            dialect = transpile_config.source_dialect
            input_file = Path(input_source) / "some_query.sql"
            sql_code = "select * from employees;"
            result = await lsp_engine.transpile(dialect, "databricks", sql_code, input_file)
            await lsp_engine.shutdown()
            transpiled = format_transpiled(result.transpiled_code)
            assert transpiled == sql_code


class PatchedMavenInstaller(MavenInstaller):

    @classmethod
    def get_current_maven_artifact_version(cls, group_id: str, artifact_id: str):
        return "0.2.0"

    @classmethod
    def download_artifact_from_maven(
        cls,
        group_id: str,
        artifact_id: str,
        version: str,
        target: Path,
        classifier: str | None = None,
        extension: str = "jar",
    ) -> bool:
        sample_jar = (
            Path(__file__).parent.parent.parent
            / "resources"
            / "transpiler_configs"
            / "morpheus"
            / "jar"
            / "morpheus-lsp-0.2.0-SNAPSHOT-jar-with-dependencies.jar"
        )
        assert sample_jar.exists()
        shutil.copyfile(sample_jar, target)
        return True


def test_java_version():
    version = WorkspaceInstaller.get_java_version()
    assert version >= 110
