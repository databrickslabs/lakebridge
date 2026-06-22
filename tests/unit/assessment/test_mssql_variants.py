from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.assessments.profiler import get_pipeline, resolve_mssql_variant

# mssql is registered as AUTO in the registry; these are the resolver's outputs / the config directories.
MSSQL_VARIANTS = ("single_db", "multi_db")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MSSQL_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/mssql"


def test_mssql_is_registered_as_auto_variant_source() -> None:
    assert SOURCE_SYSTEM_VARIANTS["mssql"] == (AUTO,)


@pytest.mark.parametrize("variant", MSSQL_VARIANTS)
def test_mssql_variants_have_pipeline_config_path(variant: str) -> None:
    cfg_path = get_pipeline("mssql", variant)
    assert f"/mssql/{variant}/pipeline_config.yml" in str(cfg_path)


@pytest.mark.parametrize("variant", MSSQL_VARIANTS)
def test_mssql_variant_config_references_existing_files(variant: str) -> None:
    """Every extract_source referenced by a variant config must point at a real SQL file."""
    config = yaml.safe_load((_MSSQL_RESOURCES / variant / "pipeline_config.yml").read_text())
    for step in config["steps"]:
        extract_source = _REPO_ROOT / step["extract_source"]
        assert extract_source.exists(), f"{variant} step '{step['name']}' references missing file {extract_source}"


@pytest.mark.parametrize(
    ("engine_edition", "expected"),
    [
        (5, "single_db"),  # Azure SQL Database
        (8, "multi_db"),  # Azure SQL Managed Instance
        (3, "multi_db"),  # on-prem Enterprise
        (2, "multi_db"),  # on-prem Standard
    ],
)
def test_resolve_mssql_variant(engine_edition: int, expected: str) -> None:
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    db_manager.fetch.return_value = MagicMock(rows=[[engine_edition]])
    with (
        patch("databricks.labs.lakebridge.assessments.profiler.DatabaseManager", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.profiler.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {}
        assert resolve_mssql_variant(Path("creds.yml")) == expected
    db_manager.fetch.assert_called_once_with("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")
