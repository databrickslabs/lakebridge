"""Unit tests for the MSSQL auth strategies and credential resolution.

Exercises each `MSSQLAuth.resolve_credentials(config)` implementation directly;
no database or sandbox required.
"""

from unittest.mock import patch

import pytest

from databricks.labs.lakebridge.connections.database_manager import MSSQLConnector
from databricks.labs.lakebridge.connections.mssql_auth import (
    AUTH_CHOICES,
    ActiveDirectoryPassword,
    ActiveDirectoryServicePrincipal,
    DefaultAzureCredential,
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


def test_default_azure_credential_emits_keyword_and_no_credentials() -> None:
    """The driver resolves the identity itself; nothing is read from config."""
    resolved = DefaultAzureCredential.resolve_credentials({})
    assert resolved.authentication_param == "ActiveDirectoryDefault"
    assert resolved.username is None
    assert resolved.password is None


def test_auth_choices_class_names_are_odbc_or_azure_literals() -> None:
    """Class names match the `Authentication=` literal, or the Azure SDK class for the default chain."""
    literals = {
        "SqlPassword",
        "DefaultAzureCredential",
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


def test_mssql_connector_applies_resolved_credentials_as_connect_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "spn-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "spn-secret")

    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect",
        return_value=object(),
    ) as connect:
        MSSQLConnector(
            {
                "auth_type": "ActiveDirectoryServicePrincipal",
                "server": "test-server",
                "port": 1433,
                "database": "master",
            }
        )

    connect.assert_called_once_with(
        autocommit=True,
        timeout=30,
        server="test-server,1433",
        database="master",
        authentication="ActiveDirectoryServicePrincipal",
        uid="spn-id",
        pwd="spn-secret",
        trust_server_certificate="no",
    )


def test_mssql_connector_sql_password_omits_authentication_keyword() -> None:
    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect",
        return_value=object(),
    ) as connect:
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

    connect.assert_called_once_with(
        autocommit=True,
        timeout=30,
        server="test-server,1433",
        database="master",
        uid="alice",
        pwd="secret",
        trust_server_certificate="no",
    )


def test_mssql_connector_passes_special_credentials_verbatim_as_kwargs() -> None:
    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect",
        return_value=object(),
    ) as connect:
        MSSQLConnector(
            {
                "server": "test-server",
                "port": 1433,
                "database": "master",
                "user": "al;ice",
                "password": "se}cr{et;=?",
            }
        )

    kwargs = connect.call_args.kwargs
    assert kwargs["uid"] == "al;ice"
    assert kwargs["pwd"] == "se}cr{et;=?"


def test_mssql_connector_default_azure_credential_has_no_uid_pwd() -> None:
    with patch(
        "databricks.labs.lakebridge.connections.database_manager.mssql_python.connect",
        return_value=object(),
    ) as connect:
        MSSQLConnector(
            {
                "auth_type": "DefaultAzureCredential",
                "server": "test-server",
                "port": 1433,
                "database": "master",
            }
        )

    connect.assert_called_once_with(
        autocommit=True,
        timeout=30,
        server="test-server,1433",
        database="master",
        authentication="ActiveDirectoryDefault",
        trust_server_certificate="no",
    )
