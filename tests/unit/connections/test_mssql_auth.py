"""Unit tests for the MSSQL auth strategies and credential resolution.

Exercises each `MSSQLAuth.resolve_credentials(config)` implementation directly;
no database or sandbox required.
"""

from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.connections.database_manager import MSSQLConnector
from databricks.labs.lakebridge.connections.mssql_auth import (
    AUTH_CHOICES,
    ActiveDirectoryInteractive,
    ActiveDirectoryPassword,
    ActiveDirectoryServicePrincipal,
    DefaultAzureCredential,
    SqlPassword,
    _AUTH_REGISTRY,
    resolve_mssql_credentials,
)


def test_sql_password_returns_user_and_password_from_config() -> None:
    resolved = SqlPassword.resolve_credentials({"user": "alice", "password": "secret"})
    assert resolved.username == "alice"
    assert resolved.password == "secret"
    assert resolved.authentication_param is None
    assert resolved.engine_kwargs == {}


def test_sql_password_missing_user_raises_key_error() -> None:
    with pytest.raises(KeyError) as exc:
        SqlPassword.resolve_credentials({"password": "secret"})
    assert "SqlPassword" in str(exc.value)


def test_sql_password_missing_password_raises_key_error() -> None:
    with pytest.raises(KeyError) as exc:
        SqlPassword.resolve_credentials({"user": "alice"})
    assert "SqlPassword" in str(exc.value)


def test_sql_password_with_none_values_raises_key_error() -> None:
    """Catches the hand-edited-YAML case where user/password are present but None."""
    with pytest.raises(KeyError):
        SqlPassword.resolve_credentials({"user": None, "password": None})


def test_active_directory_password_missing_credentials_raises_key_error() -> None:
    with pytest.raises(KeyError) as exc:
        ActiveDirectoryPassword.resolve_credentials({"user": None, "password": None})
    assert "ActiveDirectoryPassword" in str(exc.value)


def test_active_directory_password_returns_aad_param_and_credentials() -> None:
    resolved = ActiveDirectoryPassword.resolve_credentials({"user": "u@example.com", "password": "p"})
    assert resolved.username == "u@example.com"
    assert resolved.password == "p"
    assert resolved.authentication_param == "ActiveDirectoryPassword"


def test_active_directory_service_principal_resolves_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "client-secret")
    resolved = ActiveDirectoryServicePrincipal.resolve_credentials({})
    assert resolved.username == "client-id"
    assert resolved.password == "client-secret"
    assert resolved.authentication_param == "ActiveDirectoryServicePrincipal"


def test_active_directory_service_principal_missing_both_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    with pytest.raises(OSError) as exc:
        ActiveDirectoryServicePrincipal.resolve_credentials({})
    assert "AZURE_CLIENT_ID" in str(exc.value)
    assert "AZURE_CLIENT_SECRET" in str(exc.value)


def test_active_directory_service_principal_missing_only_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id")
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    with pytest.raises(OSError) as exc:
        ActiveDirectoryServicePrincipal.resolve_credentials({})
    assert "AZURE_CLIENT_SECRET" in str(exc.value)
    assert "AZURE_CLIENT_ID" not in str(exc.value)


def test_active_directory_interactive_with_user_prefill() -> None:
    resolved = ActiveDirectoryInteractive.resolve_credentials({"user": "u@example.com"})
    assert resolved.username == "u@example.com"
    assert resolved.password is None
    assert resolved.authentication_param == "ActiveDirectoryInteractive"


def test_active_directory_interactive_without_user() -> None:
    resolved = ActiveDirectoryInteractive.resolve_credentials({})
    assert resolved.username is None
    assert resolved.password is None


def test_default_azure_credential_is_wired_but_not_implemented() -> None:
    """DefaultAzureCredential is in the registry but `resolve_credentials()` raises until implemented."""
    with pytest.raises(NotImplementedError):
        DefaultAzureCredential.resolve_credentials({})


def test_default_azure_credential_is_in_registry_but_not_in_user_choices() -> None:
    assert "DefaultAzureCredential" in _AUTH_REGISTRY
    assert DefaultAzureCredential not in AUTH_CHOICES


def test_auth_choices_class_names_are_odbc_or_azure_literals() -> None:
    """Class names must match the ODBC `Authentication=` literal (or Azure SDK class name) exactly."""
    odbc_literals = {
        "SqlPassword",
        "ActiveDirectoryPassword",
        "ActiveDirectoryServicePrincipal",
        "ActiveDirectoryInteractive",
    }
    assert {cls.__name__ for cls in AUTH_CHOICES} == odbc_literals


def test_invalid_auth_type_raises_connection_error() -> None:
    with pytest.raises(ConnectionError) as exc:
        resolve_mssql_credentials({"auth_type": "bogus", "user": "u", "password": "p"})
    assert "bogus" in str(exc.value)


def test_default_auth_type_is_sql_password() -> None:
    """When no auth_type is set, dispatch to SqlPassword for backward compatibility."""
    resolved = resolve_mssql_credentials({"user": "u", "password": "p"})
    assert resolved.username == "u"
    assert resolved.password == "p"
    assert resolved.authentication_param is None


def test_invalid_legacy_auth_type_no_longer_aliased(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-rename auth_type strings are NOT recognized; users must re-configure."""
    monkeypatch.setenv("AZURE_CLIENT_ID", "id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
    with pytest.raises(ConnectionError):
        resolve_mssql_credentials({"auth_type": "spn_authentication"})


def test_mssql_connector_applies_resolved_credentials_to_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """MSSQLConnector calls resolve_mssql_credentials and applies the result to URL.create + create_engine."""
    monkeypatch.setenv("AZURE_CLIENT_ID", "spn-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "spn-secret")

    captured = {}

    def fake_create_engine(connection_string, **kwargs):
        captured["url"] = connection_string
        captured["kwargs"] = kwargs
        return object()

    with patch("databricks.labs.lakebridge.connections.database_manager.create_engine", side_effect=fake_create_engine):
        MSSQLConnector(
            {
                "auth_type": "ActiveDirectoryServicePrincipal",
                "server": "test-server",
                "port": 1433,
                "database": "master",
                "driver": "ODBC Driver 18 for SQL Server",
            }
        )

    assert "ActiveDirectoryServicePrincipal" in str(captured["url"])
    assert "spn-id" in str(captured["url"])
