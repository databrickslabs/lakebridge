import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


@pytest.fixture
def transpiler_repository(tmp_path: Path) -> TranspilerRepository:
    """A thin transpiler repository that only contains metadata for the Bladebridge and Morpheus transpilers."""
    resources_folder = Path(__file__).parent.parent.parent / "resources" / "transpiler_configs"
    labs_path = tmp_path / "labs"
    repository = TranspilerRepository(labs_path=labs_path)
    for transpiler in ("bladebridge", "morpheus"):
        install_directory = repository.transpilers_path() / transpiler
        # Just the config and state files, not the whole thing: we're only testing the repository and transpiler
        # metadata.
        for resource in (
                Path("lib") / "config.yml",
                Path("state") / "version.json",
        ):
            source = resources_folder / transpiler / resource
            target = install_directory / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return repository


def test_user_home() -> None:
    repository = TranspilerRepository.user_home()
    assert repository is not None
    # Can be called multiple times, returns the same instance.
    assert repository is TranspilerRepository.user_home()


def test_lists_all_transpiler_names(transpiler_repository: TranspilerRepository) -> None:
    transpiler_names = transpiler_repository.all_transpiler_names()
    assert transpiler_names == {'Morpheus', 'Bladebridge'}


def test_lists_all_dialects(transpiler_repository: TranspilerRepository) -> None:
    dialects = transpiler_repository.all_dialects()
    assert dialects == {
        'athena',
        'bigquery',
        'datastage',
        'greenplum',
        'informatica (desktop edition)',
        'mssql',
        'netezza',
        'oracle',
        'redshift',
        'snowflake',
        'synapse',
        'teradata',
        'tsql',
    }


def test_lists_dialect_transpilers(transpiler_repository: TranspilerRepository) -> None:
    transpilers = transpiler_repository.transpilers_with_dialect("snowflake")
    assert transpilers == {'Morpheus', 'Bladebridge'}
    transpilers = transpiler_repository.transpilers_with_dialect("datastage")
    assert transpilers == {'Bladebridge'}


@pytest.mark.parametrize(("product_name", "version"), (("morpheus", "0.4.0"), ("bladebridge", "0.1.9")))
def test_get_installed_version(transpiler_repository: TranspilerRepository, product_name: str, version: str) -> None:
    installed_version = transpiler_repository.get_installed_version(product_name)
    assert installed_version == version


@pytest.mark.parametrize("transpiler_name", ("Morpheus", "Bladebridge"))
def test_transpilers_config_path(transpiler_repository: TranspilerRepository, transpiler_name: str) -> None:
    config_path = transpiler_repository.transpiler_config_path(transpiler_name)
    assert config_path.name == "config.yml" and config_path.parent.name == "lib"
    assert config_path.is_file()


@pytest.mark.parametrize(
    ("transpiler_name", "source_dialect", "expected_options"),
    (
            ("Morpheus", "snowflake", []),
            (
                    "Bladebridge",
                    "datastage",
                    [
                            LSPConfigOptionV1(
                                flag="overrides-file",
                                method=LSPPromptMethod.QUESTION,
                                prompt="Specify the config file to override the default[Bladebridge] config - press <enter> for none",
                                choices=[],
                                default='<none>',
                            ),
                            LSPConfigOptionV1(
                                flag="target-tech",
                                method=LSPPromptMethod.CHOICE,
                                prompt="Specify which technology should be generated",
                                choices=["SPARKSQL", "PYSPARK"],
                            ),
                    ],
            ),
    ),
)
def test_transpiler_dialect_options(
    transpiler_repository: TranspilerRepository,
    transpiler_name: str,
    source_dialect: str,
    expected_options: Sequence[LSPConfigOptionV1],
) -> None:
    options = transpiler_repository.transpiler_config_options(transpiler_name, source_dialect)
    assert options == expected_options
