"""Authentication strategies for MSSQL-family ODBC connections.

Each class is named after the ODBC `Authentication=` literal it maps to (e.g.
`ActiveDirectoryServicePrincipal`), or after the Azure SDK class it uses for
token-injection paths (e.g. `DefaultAzureCredential`).

The single point of contract is `MSSQLAuth.resolve_credentials(config)` — each class
returns a fully-resolved `ResolvedCredentials` that the connector applies verbatim
to the connection string and `create_engine` call. No conventions, no class
attributes acting as switches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from databricks.labs.blueprint.installation import JsonObject


@dataclass(frozen=True)
class ResolvedCredentials:
    """Everything `MSSQLConnector` needs to open a connection.

    - `authentication_param`: value of the ODBC `Authentication=` query parameter.
    - `username` / `password`: included in the SQLAlchemy URL when non-None.
    - `engine_kwargs`: forwarded to `sqlalchemy.create_engine` (used by token-injection paths).
    """

    authentication_param: str
    username: str | None = None
    password: str | None = None
    engine_kwargs: dict[str, Any] = field(default_factory=dict)


class MSSQLAuth(Protocol):
    """Authentication strategy. Single classmethod takes a config dict and returns resolved credentials."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials: ...


def _require_user_password(auth_name: str, config: JsonObject) -> tuple[str, str]:
    if not config.get("user") or not config.get("password"):
        raise KeyError(f"{auth_name} requires non-empty 'user' and 'password' in config")
    return str(config["user"]), str(config["password"])


class SqlPassword:
    """SQL Authentication — username and password from the credentials file."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials:
        user, password = _require_user_password(cls.__name__, config)
        return ResolvedCredentials(
            authentication_param="SqlPassword",
            username=user,
            password=password,
        )


class ActiveDirectoryPassword:
    """Entra ID (Azure AD) username + password. Not MFA-capable."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials:
        user, password = _require_user_password(cls.__name__, config)
        return ResolvedCredentials(
            authentication_param="ActiveDirectoryPassword",
            username=user,
            password=password,
        )


class ActiveDirectoryServicePrincipal:
    """Service Principal — credentials sourced from AZURE_CLIENT_ID / AZURE_CLIENT_SECRET env vars."""

    _CLIENT_ID_VAR = "AZURE_CLIENT_ID"
    _CLIENT_SECRET_VAR = "AZURE_CLIENT_SECRET"

    @classmethod
    def resolve_credentials(cls, _config: JsonObject) -> ResolvedCredentials:
        missing = [v for v in (cls._CLIENT_ID_VAR, cls._CLIENT_SECRET_VAR) if not os.environ.get(v)]
        if missing:
            raise OSError(f"ActiveDirectoryServicePrincipal requires env vars: {', '.join(missing)}")
        return ResolvedCredentials(
            authentication_param="ActiveDirectoryServicePrincipal",
            username=os.environ[cls._CLIENT_ID_VAR],
            password=os.environ[cls._CLIENT_SECRET_VAR],
        )


class ActiveDirectoryInteractive:
    """Browser-based interactive auth. MFA-capable; the driver opens a browser at connect time."""

    @classmethod
    def resolve_credentials(cls, config: JsonObject) -> ResolvedCredentials:
        user_value = config.get("user")
        return ResolvedCredentials(
            username=str(user_value) if user_value else None,
            authentication_param="ActiveDirectoryInteractive",
        )


class DefaultAzureCredential:
    """Token injection via Azure SDK `DefaultAzureCredential` + `SQL_COPT_SS_ACCESS_TOKEN`.

    Wired into the dispatch surface so the configurator and registry know about it,
    but not yet implemented. A follow-up PR fills in `resolve_credentials` with the
    token-acquisition + `attrs_before` plumbing — no other class needs to change.
    """

    @classmethod
    def resolve_credentials(cls, _config: JsonObject) -> ResolvedCredentials:
        raise NotImplementedError(
            "DefaultAzureCredential token injection is not yet implemented; pick another auth method."
        )


# User-selectable auth methods, in the order they appear in the configurator prompt.
# `DefaultAzureCredential` is intentionally excluded until its `resolve_credentials()` is implemented.
AUTH_CHOICES: list[type[MSSQLAuth]] = [
    SqlPassword,
    ActiveDirectoryPassword,
    ActiveDirectoryServicePrincipal,
    ActiveDirectoryInteractive,
]

_AUTH_REGISTRY: dict[str, type[MSSQLAuth]] = {cls.__name__: cls for cls in AUTH_CHOICES}


def resolve_mssql_credentials(config: JsonObject) -> ResolvedCredentials:
    """Look up the configured `auth_type`, dispatch to the matching class, return resolved credentials."""
    auth_type = str(config.get("auth_type", SqlPassword.__name__))
    cls = _AUTH_REGISTRY.get(auth_type)
    if cls is None:
        raise ConnectionError(f"Invalid MSSQL auth_type: {auth_type}")
    return cls.resolve_credentials(config)
