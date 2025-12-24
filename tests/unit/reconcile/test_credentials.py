import logging
import pytest

from databricks.labs.lakebridge.reconcile.connectors.credentials import (
    ReconcileCredentialsConfig,
    build_recon_creds,
    validate_creds,
)


def test_databricks_source_returns_none():
    assert build_recon_creds("databricks", "scope") is None


def test_build_unsupported_source_raises():
    with pytest.raises(ValueError, match="Unsupported source system: unknown"):
        build_recon_creds("unknown", "scope")


@pytest.mark.parametrize("source", ["oracle", "mssql", "synapse"])
def test_non_snowflake_sources_build_expected_mapping(source):
    scope = "my-scope"
    cfg = build_recon_creds(source, scope)

    assert isinstance(cfg, ReconcileCredentialsConfig)
    assert cfg.vault_type == "databricks"

    required = [
        "host",
        "port",
        "database",
        "user",
        "password",
    ]
    for k in required:
        assert cfg.vault_secret_names[k] == f"{scope}/{k}"


def test_snowflake_adds_extra_keys_and_logs_warning(caplog):
    logger = "databricks.labs.lakebridge.reconcile.connectors.credentials"
    scope = "sf-scope"
    with caplog.at_level(logging.WARNING, logger):
        cfg = build_recon_creds("snowflake", scope)

    # warning logged
    assert any("Please specify the Snowflake authentication method" in r.message for r in caplog.records)

    # snowflake adds pem_private_key and sfPassword
    assert cfg.vault_secret_names["pem_private_key"] == f"{scope}/pem_private_key"
    assert cfg.vault_secret_names["sfPassword"] == f"{scope}/sfPassword"


def test_validate_unsupported_source_raises():
    cfg = ReconcileCredentialsConfig("databricks", {})
    with pytest.raises(ValueError, match="Unsupported source system: unknown"):
        validate_creds(cfg, "unknown")


@pytest.mark.parametrize("source", ["oracle", "mssql", "synapse"])
def test_missing_required_keys_raise(source):
    creds = ReconcileCredentialsConfig(
        "databricks",
        {"host": "scope/host", "user": "scope/user"},
    )

    with pytest.raises(ValueError):
        validate_creds(creds, source)
