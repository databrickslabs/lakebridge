from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from databricks.labs.lakebridge.assessments import SOURCE_SYSTEM_VARIANTS, AUTO
from databricks.labs.lakebridge.assessments.profiler import get_pipeline
from databricks.labs.lakebridge.assessments.variants import resolve_mssql_variant, resolve_variant

# mssql is registered as AUTO in the registry; these are the resolver's outputs / the config directories.
MSSQL_VARIANTS = ("single_db", "multi_db")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MSSQL_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/mssql"


def test_mssql_is_registered_as_auto_variant_source() -> None:
    assert SOURCE_SYSTEM_VARIANTS["mssql"] == (AUTO,)


def test_unified_sources_have_no_fixed_variant_choices() -> None:
    """Redshift and Teradata use a single pipeline config without variant subpaths."""
    assert "redshift" not in SOURCE_SYSTEM_VARIANTS
    assert "teradata" not in SOURCE_SYSTEM_VARIANTS


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
    ("source", "variant"),
    [
        ("snowflake", AUTO),
        ("snowflake", "anything"),
        ("redshift", "provisioned"),
        ("teradata", "core"),
    ],
)
def test_resolve_variant_no_variants_returns_none(source: str, variant: str | None) -> None:
    assert resolve_variant(source, variant) is None


def test_resolve_variant_auto_source_ignores_explicit_variant() -> None:
    # An AUTO source always auto-detects; an explicit variant is ignored and the resolver still runs.
    assert (
        resolve_variant(
            "mssql", "single_db", resolvers={"mssql": lambda cred_file_path: "multi_db"}, cred_file_path=Path("x")
        )
        == "multi_db"
    )


def test_resolve_variant_auto_source_probes_resolver() -> None:
    assert (
        resolve_variant(
            "mssql", AUTO, resolvers={"mssql": lambda cred_file_path: "multi_db"}, cred_file_path=Path("creds.yml")
        )
        == "multi_db"
    )


def test_resolve_variant_auto_source_none_probes_resolver() -> None:
    assert resolve_variant("mssql", None, resolvers={"mssql": lambda cred_file_path: "single_db"}) == "single_db"


@pytest.mark.parametrize(
    ("engine_edition", "expected"),
    [
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
        patch("databricks.labs.lakebridge.assessments.variants.DatabaseManager", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {}
        assert resolve_mssql_variant(Path("creds.yml")) == expected
    db_manager.fetch.assert_called_once_with("SELECT CAST(SERVERPROPERTY('EngineEdition') AS INT) AS engine_edition")


def test_resolve_mssql_variant_azure_sql_db_without_database_raises() -> None:
    """Azure SQL Database (edition 5) with no concrete database must not silently profile master."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    db_manager.fetch.return_value = MagicMock(rows=[[5]])  # Azure SQL Database
    with (
        patch("databricks.labs.lakebridge.assessments.variants.DatabaseManager", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"database": "*"}
        with pytest.raises(ValueError, match="Azure SQL Database"):
            resolve_mssql_variant(Path("creds.yml"))


def test_resolve_mssql_variant_with_configured_database_skips_probe() -> None:
    """A configured database scopes profiling to that database (single_db) without probing the edition."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    with (
        patch("databricks.labs.lakebridge.assessments.variants.DatabaseManager", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = {"database": "AdventureWorks"}
        assert resolve_mssql_variant(Path("creds.yml")) == "single_db"
    db_manager.fetch.assert_not_called()


@pytest.mark.parametrize("database", ["*", "  ", "", None])
def test_resolve_mssql_variant_all_databases_probes(database) -> None:
    """The '*' sentinel and blank/whitespace all mean 'all databases' -> probe the edition, not single_db."""
    db_manager = MagicMock()
    db_manager.__enter__.return_value = db_manager
    db_manager.fetch.return_value = MagicMock(rows=[[3]])  # on-prem Enterprise -> multi_db
    config = {} if database is None else {"database": database}
    with (
        patch("databricks.labs.lakebridge.assessments.variants.DatabaseManager", return_value=db_manager),
        patch("databricks.labs.lakebridge.assessments.variants.create_credential_manager") as cred_manager,
    ):
        cred_manager.return_value.get_credentials.return_value = config
        assert resolve_mssql_variant(Path("creds.yml")) == "multi_db"
