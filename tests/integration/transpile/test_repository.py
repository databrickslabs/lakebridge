import shutil
from collections.abc import Generator
from pathlib import Path

import pytest

from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


@pytest.fixture
def transpiler_repository(tmp_path: Path) -> Generator[TranspilerRepository, None, None]:
    """A thin transpiler repository that only contains metadata for the Bladebridge and Morpheus transpilers."""
    resources_folder = Path(__file__).parent.parent.parent / "resources" / "transpiler_configs"
    labs_path = tmp_path / "labs"
    repository = TranspilerRepository(labs_path=labs_path)
    for transpiler in ("bladebridge", "morpheus"):
        install_directory = repository.transpilers_path() / transpiler / "lib"
        install_directory.mkdir(parents=True)
        source = resources_folder / transpiler / "lib" / "config.yml"
        target = install_directory / "config.yml"
        # Just the config file, not the whole thing: we're only testing the repository and transpiler metadata.
        shutil.copyfile(source, target)
    yield repository


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
