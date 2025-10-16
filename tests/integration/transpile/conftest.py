import shutil
from pathlib import Path

import pytest

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
