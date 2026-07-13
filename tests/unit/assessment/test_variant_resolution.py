from pathlib import Path

from databricks.labs.lakebridge.assessments import AUTO
from databricks.labs.lakebridge.assessments.variants import resolve_variant


def test_resolve_variant_no_variants_returns_none():
    assert resolve_variant("snowflake", AUTO) is None


def test_resolve_variant_no_variants_ignores_explicit():
    assert resolve_variant("snowflake", "anything") is None


def test_resolve_variant_unified_redshift_ignores_explicit_variant():
    assert resolve_variant("redshift", "provisioned") is None


def test_resolve_variant_auto_source_ignores_explicit_variant():
    # An AUTO source always auto-detects; an explicit variant is ignored and the resolver still runs.
    assert (
        resolve_variant(
            "mssql", "single_db", resolvers={"mssql": lambda cred_file_path: "multi_db"}, cred_file_path=Path("x")
        )
        == "multi_db"
    )


def test_resolve_variant_auto_source_probes_resolver():
    assert (
        resolve_variant(
            "mssql", AUTO, resolvers={"mssql": lambda cred_file_path: "multi_db"}, cred_file_path=Path("creds.yml")
        )
        == "multi_db"
    )


def test_resolve_variant_auto_source_none_probes_resolver():
    assert resolve_variant("mssql", None, resolvers={"mssql": lambda cred_file_path: "single_db"}) == "single_db"
