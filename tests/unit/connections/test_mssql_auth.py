"""Unit tests for the MSSQL auth strategies and credential resolution.

Exercises each `MSSQLAuth.resolve_credentials(config)` implementation directly;
no database or sandbox required.
"""

from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.connections.database_manager import MSSQLConnector
from databricks.labs.lakebridge.connections.mssql_auth import (
    AUTH_CHOICES,
    ActiveDirectoryDefault,
    ActiveDirectoryPassword,
    ActiveDirectoryServicePrincipal,
    SqlPassword,
    resolve_mssql_credentials,
)


def test_sql_password_returns_user_and_password_from_config() -> None:
    resolved = SqlPassword.resolve_credentials({"user": "alice", "password": "secret"})
    assert resolved.username == "alice"
    assert resolved.password == "secret"
    # Plain UID/PWD SQL auth: no Authentication= keyword is emitted
    assert resolved.authentication_param is None


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


def test_active_directory_default_emits_keyword_and_no_credentials() -> None:
    """The driver resolves the identity itself; nothing is read from config."""
    resolved = ActiveDirectoryDefault.resolve_credentials({})
    assert resolved.authentication_param == "ActiveDirectoryDefault"
    assert resolved.username is None
    assert resolved.password is None


def test_active_directory_default_is_dispatchable() -> None:
    resolved = resolve_mssql_credentials({"auth_type": "ActiveDirectoryDefault"})
    assert resolved.authentication_param == "ActiveDirectoryDefault"


def test_auth_choices_class_names_are_authentication_literals() -> None:
    """Class names must match the `Authentication=` connection-string literal exactly."""
    literals = {
        "SqlPassword",
        "ActiveDirectoryDefault",
        "ActiveDirectoryPassword",
        "ActiveDirectoryServicePrincipal",
    }
    assert {cls.__name__ for cls in AUTH_CHOICES} == literals


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


def test_mssql_connector_applies_resolved_credentials_to_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MSSQLConnector calls resolve_mssql_credentials and applies the result to mssql_python.connect."""
    monkeypatch.setenv("AZURE_CLIENT_ID", "spn-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "spn-secret")

    captured = {}

    def fake_connect(connection_string, **kwargs):
        captured["connection_string"] = connection_string
        captured["kwargs"] = kwargs
        return object()

    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect", side_effect=fake_connect
    ):
        MSSQLConnector(
            {
                "auth_type": "ActiveDirectoryServicePrincipal",
                "server": "test-server",
                "port": 1433,
                "database": "master",
            }
        )

    assert "Server=test-server,1433" in captured["connection_string"]
    assert "Authentication=ActiveDirectoryServicePrincipal" in captured["connection_string"]
    assert "UID=spn-id" in captured["connection_string"]
    assert captured["kwargs"]["timeout"] == 30


def test_mssql_connector_sql_password_omits_authentication_keyword() -> None:
    """SQL auth is plain UID/PWD; a legacy `driver` key from old credential files is ignored."""
    captured = {}

    def fake_connect(connection_string, **kwargs):
        captured["connection_string"] = connection_string
        return object()

    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect", side_effect=fake_connect
    ):
        MSSQLConnector(
            {
                "server": "test-server",
                "port": 1433,
                "database": "master",
                "user": "alice",
                "password": "secret",
                "driver": "ODBC Driver 18 for SQL Server",
            }
        )

    assert "Authentication=" not in captured["connection_string"]
    assert "UID=alice" in captured["connection_string"]
    assert "PWD=secret" in captured["connection_string"]


def test_mssql_connector_active_directory_default_has_no_uid_pwd() -> None:
    """ActiveDirectoryDefault delegates identity to the driver: keyword only, no credentials."""
    captured = {}

    def fake_connect(connection_string, **kwargs):
        captured["connection_string"] = connection_string
        return object()

    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect", side_effect=fake_connect
    ):
        MSSQLConnector(
            {
                "auth_type": "ActiveDirectoryDefault",
                "server": "test-server",
                "port": 1433,
                "database": "master",
            }
        )

    assert "Authentication=ActiveDirectoryDefault" in captured["connection_string"]
    assert "UID=" not in captured["connection_string"]
    assert "PWD=" not in captured["connection_string"]
