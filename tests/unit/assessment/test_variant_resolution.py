from pathlib import Path
from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.assessments import AUTO
from databricks.labs.lakebridge.assessments.profiler import resolve_variant

_RESOLVERS = "databricks.labs.lakebridge.assessments.profiler.VARIANT_RESOLVERS"


def test_resolve_variant_no_variants_returns_none():
    assert resolve_variant("snowflake", AUTO) is None


def test_resolve_variant_no_variants_ignores_explicit():
    assert resolve_variant("snowflake", "anything") is None


@pytest.mark.parametrize(("given", "expected"), [("provisioned", "provisioned"), ("PROVISIONED", "provisioned")])
def test_resolve_variant_explicit_validated_for_tuple_source(given, expected):
    assert resolve_variant("redshift", given) == expected


def test_resolve_variant_explicit_invalid_for_tuple_source_raises():
    with pytest.raises(ValueError, match="Invalid variant"):
        resolve_variant("redshift", "bogus")


def test_resolve_variant_tuple_multi_variant_with_auto_raises():
    # The CLI prompts tuple sources, so AUTO reaching the loader for one is a defensive guard.
    with pytest.raises(ValueError, match="Invalid variant"):
        resolve_variant("redshift", AUTO)


def test_resolve_variant_auto_source_ignores_explicit_variant():
    # An AUTO source always auto-detects; an explicit variant is ignored and the resolver still runs.
    with patch.dict(_RESOLVERS, {"mssql": lambda cred_file_path: "multi_db"}):
        assert resolve_variant("mssql", "single_db", cred_file_path=Path("x")) == "multi_db"


def test_resolve_variant_auto_source_probes_resolver():
    with patch.dict(_RESOLVERS, {"mssql": lambda cred_file_path: "multi_db"}):
        assert resolve_variant("mssql", AUTO, cred_file_path=Path("creds.yml")) == "multi_db"


def test_resolve_variant_auto_source_none_probes_resolver():
    with patch.dict(_RESOLVERS, {"mssql": lambda cred_file_path: "single_db"}):
        assert resolve_variant("mssql", None) == "single_db"
