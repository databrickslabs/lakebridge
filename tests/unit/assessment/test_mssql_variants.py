from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.assessments.profiler import get_pipeline
from databricks.labs.lakebridge.assessments.variants import resolve_mssql_variant

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
        (8, "multi_db"),  # Azure SQL Managed Instance
        (3, "multi_db"),  # on-prem Enterprise
        (2, "multi_db"),  # on-prem Standard
    ],
)
def test_resolve_mssql_variant(engine_edition: int, expected: str) -> None:
    connector = MagicMock()
    connector.__enter__.return_value = connector
    connector.fetch.return_value = MagicMock(rows=[[engine_edition]])
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=connector),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {}
        assert resolve_mssql_variant(Path("creds.yml")) == expected
    connector.fetch.assert_called_once_with("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")


def test_resolve_mssql_variant_azure_sql_db_without_database_raises() -> None:
    """Azure SQL Database (edition 5) with no concrete database must not silently profile master."""
    connector = MagicMock()
    connector.__enter__.return_value = connector
    connector.fetch.return_value = MagicMock(rows=[[5]])  # Azure SQL Database
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=connector),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"database": "*"}
        with pytest.raises(ValueError, match="Azure SQL Database"):
            resolve_mssql_variant(Path("creds.yml"))


def test_resolve_mssql_variant_with_configured_database_skips_probe() -> None:
    """A configured database scopes profiling to that database (single_db) without probing the edition."""
    connector = MagicMock()
    connector.__enter__.return_value = connector
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=connector),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"database": "AdventureWorks"}
        assert resolve_mssql_variant(Path("creds.yml")) == "single_db"
    connector.fetch.assert_not_called()


@pytest.mark.parametrize("database", ["*", "  ", "", None])
def test_resolve_mssql_variant_all_databases_probes(database) -> None:
    """The '*' sentinel and blank/whitespace all mean 'all databases' -> probe the edition, not single_db."""
    connector = MagicMock()
    connector.__enter__.return_value = connector
    connector.fetch.return_value = MagicMock(rows=[[3]])  # on-prem Enterprise -> multi_db
    config = {} if database is None else {"database": database}
    with (
        patch("databricks.labs.lakebridge.assessments.variants.create_connector", return_value=connector),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = config
        assert resolve_mssql_variant(Path("creds.yml")) == "multi_db"
