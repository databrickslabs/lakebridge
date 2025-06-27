import os
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from databricks.labs.lakebridge.install import TranspilerInstaller


def format_transpiled(sql: str) -> str:
    parts = sql.lower().split("\n")
    stripped = [s.strip() for s in parts]
    sql = " ".join(stripped)
    sql = sql.replace(";;", ";")
    return sql


base_cwd = os.getcwd()


def test_installs_and_runs_local_bladebridge(bladebridge_artifact):
    os.chdir(base_cwd)
    try:
        # TODO temporary workaround for RecursionError with temp dirs on Windows
        if sys.platform == "win32":
            _install_and_run_pypi_bladebridge()
        else:
            with TemporaryDirectory() as tmpdir:
                with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
                    _install_and_run_local_bladebridge(bladebridge_artifact)
    finally:
        os.chdir(base_cwd)


def _install_and_run_local_bladebridge(bladebridge_artifact: Path):
    # TODO: Test that running with existing install does nothing
    # TODO: Test that running with legacy install upgrades it
    # check new install
    bladebridge = TranspilerInstaller.transpilers_path() / "bladebridge"
    # TODO temporary workaround for RecursionError with temp dirs on Windows
    if sys.platform == "win32" and bladebridge.exists():
        shutil.rmtree(bladebridge)
    assert not bladebridge.exists()
    # fresh install
    TranspilerInstaller.install_from_pypi("bladebridge", "databricks-bb-plugin", bladebridge_artifact)
    # check file-level installation
    config_path = bladebridge / "lib" / "config.yml"
    assert config_path.exists()
    version_path = bladebridge / "state" / "version.json"
    assert version_path.exists()


def test_installs_and_runs_pypi_bladebridge():
    os.chdir(base_cwd)
    try:
        # TODO temporary workaround for RecursionError with temp dirs on Windows
        if sys.platform == "win32":
            _install_and_run_pypi_bladebridge()
        else:
            with TemporaryDirectory() as tmpdir:
                with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
                    _install_and_run_pypi_bladebridge()
    finally:
        os.chdir(base_cwd)


def _install_and_run_pypi_bladebridge():
    # TODO: Test that running with existing install does nothing
    # TODO: Test that running with legacy install upgrades it
    # check new install
    bladebridge = TranspilerInstaller.transpilers_path() / "bladebridge"
    # TODO temporary workaround for RecursionError with temp dirs on Windows
    if sys.platform == "win32" and bladebridge.exists():
        shutil.rmtree(bladebridge)
    assert not bladebridge.exists()
    # fresh install
    TranspilerInstaller.install_from_pypi("bladebridge", "databricks-bb-plugin")
    # check file-level installation
    config_path = bladebridge / "lib" / "config.yml"
    assert config_path.exists()
    version_path = bladebridge / "state" / "version.json"
    assert version_path.exists()


def test_installs_and_runs_local_morpheus(morpheus_artifact):
    os.chdir(base_cwd)
    try:
        # TODO temporary workaround for RecursionError with temp dirs on Windows
        if sys.platform == "win32":
            _install_and_run_pypi_bladebridge()
        else:
            with TemporaryDirectory() as tmpdir:
                with patch.object(TranspilerInstaller, "labs_path", return_value=Path(tmpdir)):
                    _install_and_run_local_morpheus(morpheus_artifact)
    finally:
        os.chdir(base_cwd)


def _install_and_run_local_morpheus(morpheus_artifact):
    # TODO: Test that running with existing install does nothing
    # TODO: Test that running with legacy install upgrades it
    # check new install
    morpheus = TranspilerInstaller.transpilers_path() / "morpheus"
    assert not morpheus.exists()
    # fresh install
    TranspilerInstaller.install_from_maven(
        "morpheus", "com.databricks.labs", "databricks-morph-plugin", morpheus_artifact
    )
    # check file-level installation
    morpheus = TranspilerInstaller.transpilers_path() / "morpheus"
    config_path = morpheus / "lib" / "config.yml"
    assert config_path.exists()
    main_path = morpheus / "lib" / "databricks-morph-plugin.jar"
    assert main_path.exists()
    version_path = morpheus / "state" / "version.json"
    assert version_path.exists()
